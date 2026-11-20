"""
Step 10 -- Gmail inbox integration. Lists candidate audit-request emails from
a real mailbox and extracts their text (body or PDF attachment) so they can
be fed into the same decompose() -> run_checks() -> pack() -> letter()
pipeline as the sample requests. Can also send the drafted reply back to the
original sender -- but ONLY when a human in the UI explicitly clicks Send;
nothing in this file or api/main.py ever calls send_reply() on its own.

Single mailbox, single OAuth grant: the refresh token lives in
audit.oauth_tokens (one row, provider='gmail'). This app never sees or
stores your Google password -- only the token your own consent screen grants
it, scoped to gmail.readonly + gmail.send (list/read messages, and send only
when asked -- never delete, archive, or modify anything else in the mailbox).

You never see your inbox's full contents through an LLM here either --
list_candidate_messages() only returns metadata (subject/from/date/snippet)
for the UI to show as a picklist; the LLM only sees the body of whichever
single message a human explicitly clicks "run" on.
"""
import base64
import email
import mimetypes
from email import encoders
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import parseaddr
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build

from . import db
from .config import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REDIRECT_URI, GMAIL_SEARCH_QUERY

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"]


def _client_config():
    return {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }


def is_configured() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


# google-auth-oauthlib 1.x auto-generates a PKCE code_verifier on
# authorization_url() (autogenerate_code_verifier=True by default) and embeds
# its code_challenge in the URL Google redirects the user to. The callback
# comes back as a SEPARATE HTTP request/Flow instance, so that code_verifier
# has to be carried across the redirect ourselves or Google's token endpoint
# rejects the exchange with "invalid_grant: Missing code verifier" -- keyed
# by the OAuth `state` param, which Google echoes back verbatim. In-memory is
# fine here: single process, single operator, and an entry only needs to
# survive the few seconds of the redirect round-trip.
_pending_pkce = {}


def build_auth_url() -> str:
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)
    url, state = flow.authorization_url(access_type="offline", prompt="consent", include_granted_scopes="true")
    _pending_pkce[state] = flow.code_verifier
    return url


def exchange_code(code: str, state: str = None) -> None:
    """Called from the OAuth callback with Google's one-time code (+ the
    state it was issued under). Stores the refresh token -- this is the ONLY
    place a token is written from a fresh grant; every later use just
    refreshes it in place."""
    flow = Flow.from_client_config(_client_config(), scopes=SCOPES, redirect_uri=GOOGLE_REDIRECT_URI)
    flow.code_verifier = _pending_pkce.pop(state, None)
    flow.fetch_token(code=code)
    creds = flow.credentials
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO audit.oauth_tokens (provider, refresh_token, access_token, expiry, scope, updated_at)
               VALUES ('gmail', %s, %s, %s, %s, now())
               ON CONFLICT (provider) DO UPDATE SET
                 refresh_token = EXCLUDED.refresh_token, access_token = EXCLUDED.access_token,
                 expiry = EXCLUDED.expiry, scope = EXCLUDED.scope, updated_at = now()""",
            (creds.refresh_token, creds.token, creds.expiry, " ".join(creds.scopes or SCOPES)),
        )
        conn.commit()


def is_connected() -> bool:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM audit.oauth_tokens WHERE provider = 'gmail'")
        return cur.fetchone() is not None


def _load_credentials() -> Credentials:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT refresh_token, access_token, expiry, scope FROM audit.oauth_tokens WHERE provider = 'gmail'")
        row = cur.fetchone()
    if not row:
        raise RuntimeError("Gmail is not connected yet -- visit /api/inbox/auth-url first")

    creds = Credentials(
        token=row["access_token"],
        refresh_token=row["refresh_token"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        scopes=(row["scope"] or " ".join(SCOPES)).split(),
    )
    if not creds.valid:
        creds.refresh(Request())
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE audit.oauth_tokens SET access_token = %s, expiry = %s, updated_at = now() WHERE provider = 'gmail'",
                (creds.token, creds.expiry),
            )
            conn.commit()
    return creds


def _service():
    return build("gmail", "v1", credentials=_load_credentials(), cache_discovery=False)


def _find_part(payload, mime_type):
    if payload.get("mimeType") == mime_type and payload.get("body", {}).get("data"):
        return payload["body"]["data"]
    for part in payload.get("parts", []) or []:
        found = _find_part(part, mime_type)
        if found:
            return found
    return None


ACCOUNT_LIST_EXT = (".csv", ".xlsx", ".xls")
# This app's own evidence-pack filenames. A reply we sent can land back in the same
# mailbox (self-addressed test mail, a mailing list, an auto-forward), and gap_report.xlsx
# is a spreadsheet -- without this it would be picked up as if it were an Annexure A,
# i.e. the agent's own output fed back in as its next input.
OWN_ARTIFACTS = ("gap_report.xlsx", "request_interpretation.md", "response_letter_DRAFT.docx")


def _find_attachment(payload, exts, skip_names=()):
    """First attachment whose filename ends in one of `exts`, searched depth-first."""
    fn = payload.get("filename", "")
    if (fn.lower().endswith(exts) and fn.lower() not in [s.lower() for s in skip_names]
            and payload.get("body", {}).get("attachmentId")):
        return fn, payload["body"]["attachmentId"]
    for part in payload.get("parts", []) or []:
        found = _find_attachment(part, exts, skip_names)
        if found:
            return found
    return None


def _find_pdf_attachment(payload):
    return _find_attachment(payload, (".pdf",))


def _download(svc, message_id: str, attachment_id: str) -> bytes:
    att = svc.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
    return base64.urlsafe_b64decode(att["data"])


def find_account_list(message_id: str):
    """The Annexure A spreadsheet a real audit request attaches (.csv/.xlsx/.xls).
    Returns (filename, bytes) or None.

    Deliberately separate from get_message_text(): that reads the request PROSE
    (and prefers a PDF letter), while this is the list of accounts to test. An
    email whose account ids live only in a spreadsheet attachment used to be
    untestable from the inbox -- the ids were never in the text, so neither the
    auto-start regex nor _resolve_sample could find anything."""
    svc = _service()
    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    found = _find_attachment(msg["payload"], ACCOUNT_LIST_EXT, skip_names=OWN_ARTIFACTS)
    if not found:
        return None
    filename, attachment_id = found
    return filename, _download(svc, message_id, attachment_id)


def own_address() -> str:
    """The connected mailbox's own address (cached) -- used to never list mail we sent."""
    global _own
    if _own is None:
        try:
            _own = _service().users().getProfile(userId="me").execute().get("emailAddress", "").lower()
        except Exception:
            _own = ""
    return _own


_own = None


def list_candidate_messages(max_results: int = 30, skip_ids: set | None = None) -> list[dict]:
    """Only INCOMING mail (never the Sent folder or anything from this mailbox),
    and only metadata for ids not already in `skip_ids` -- so a refresh costs
    one list call plus one fetch per genuinely new message."""
    svc = _service()
    q = f"{GMAIL_SEARCH_QUERY} -in:sent -from:me"
    resp = svc.users().messages().list(userId="me", q=q, maxResults=max_results).execute()
    out = []
    for m in resp.get("messages", []):
        if skip_ids and m["id"] in skip_ids:
            continue
        msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                         metadataHeaders=["Subject", "From", "Date"]).execute()
        headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
        has_pdf = _find_pdf_attachment(msg["payload"]) is not None
        out.append({
            "message_id": m["id"],
            "subject": headers.get("Subject", "(no subject)"),
            "from": headers.get("From", ""),
            "date": headers.get("Date", ""),
            "snippet": msg.get("snippet", ""),
            "has_pdf": has_pdf,
        })
    return out


def get_message_meta(message_id: str) -> dict:
    """Reply-threading metadata for ONE message, fetched fresh at send time --
    never trusts anything the browser claims about who the recipient is."""
    svc = _service()
    msg = svc.users().messages().get(userId="me", id=message_id, format="metadata",
                                     metadataHeaders=["Subject", "From", "Message-Id", "Message-ID"]).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"].get("headers", [])}
    sender = headers.get("From", "")
    name, addr = parseaddr(sender)       # "Likhith Gowda <x@y>" -> ("Likhith Gowda", "x@y")
    return {
        "from": sender,
        "from_name": name or (addr.split("@")[0] if addr else ""),
        "subject": headers.get("Subject", "(no subject)"),
        "message_id_header": headers.get("Message-Id") or headers.get("Message-ID", ""),
        "thread_id": msg.get("threadId"),
    }


def get_message_text(message_id: str) -> str:
    """Prefers a PDF attachment's extracted text (matches how REQ-02 is
    handled today); falls back to the plain-text body."""
    svc = _service()
    msg = svc.users().messages().get(userId="me", id=message_id, format="full").execute()
    payload = msg["payload"]

    pdf = _find_pdf_attachment(payload)
    if pdf:
        filename, attachment_id = pdf
        att = svc.users().messages().attachments().get(userId="me", messageId=message_id, id=attachment_id).execute()
        pdf_bytes = base64.urlsafe_b64decode(att["data"])
        import io
        import pdfplumber
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf_doc:
            return "\n".join(p.extract_text() or "" for p in pdf_doc.pages)

    data = _find_part(payload, "text/plain")
    if data:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")

    html = _find_part(payload, "text/html")
    if html:
        raw = base64.urlsafe_b64decode(html).decode("utf-8", errors="replace")
        return email.message_from_string(raw).get_payload() if raw else ""

    raise ValueError(f"message {message_id} has neither a PDF attachment nor a text body")


def _html_body(body_text: str) -> str:
    """The letter's results table is a plain-text pipe table, which only lines
    up in a monospace font -- most mail clients use a proportional one, so it
    arrives as ragged garbage. Send multipart/alternative and give the HTML
    part a real <table>; clients that only do plain text still get the
    original text unchanged."""
    from html import escape

    from .letter import iter_blocks

    out = []
    for kind, block in iter_blocks(body_text):
        if kind == "table":
            head, *rows = block
            cells = "".join(
                f'<th align="left" style="padding:6px 10px;border:1px solid #ccc;'
                f'background:#f4f4f4">{escape(c)}</th>' for c in head)
            body = "".join(
                "<tr>" + "".join(
                    f'<td style="padding:6px 10px;border:1px solid #ccc">{escape(c)}</td>'
                    for c in r) + "</tr>"
                for r in rows)
            out.append(f'<table style="border-collapse:collapse;font-size:14px;margin:12px 0">'
                       f"<tr>{cells}</tr>{body}</table>")
        else:
            out.append(f'<p style="margin:0 0 12px">{escape(block).replace(chr(10), "<br>")}</p>')
    return ('<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;'
            f'line-height:1.5;color:#222">{"".join(out)}</div>')


def send_reply(to: str, subject: str, body_text: str, thread_id: str = None, in_reply_to: str = None,
               attachments: list | None = None) -> str:
    """Sends ONE email. Called only from api/main.py's /send-reply endpoint,
    which only runs when a human clicks Send in the UI -- there is no
    scheduler, webhook, or automatic trigger anywhere that calls this. Threads
    the reply into the original conversation when thread_id/in_reply_to are
    given (both come from get_message_meta() on the real inbound message,
    never from client-supplied values).

    attachments: list of Path. The body is always multipart/alternative
    (plain + HTML); with attachments that gets nested inside a
    multipart/mixed, which is the shape mail clients expect."""
    alt = MIMEMultipart("alternative")
    alt.attach(MIMEText(body_text, "plain"))
    alt.attach(MIMEText(_html_body(body_text), "html"))

    if attachments:
        msg = MIMEMultipart("mixed")
        msg.attach(alt)
        for path in attachments:
            path = Path(path)
            if not path.exists():
                continue
            ctype, _ = mimetypes.guess_type(path.name)
            maintype, _, subtype = (ctype or "application/octet-stream").partition("/")
            part = MIMEBase(maintype, subtype)
            part.set_payload(path.read_bytes())
            encoders.encode_base64(part)
            part.add_header("Content-Disposition", "attachment", filename=path.name)
            msg.attach(part)
    else:
        msg = alt

    msg["to"] = to
    msg["subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id

    svc = _service()
    sent = svc.users().messages().send(userId="me", body=body).execute()
    return sent["id"]
