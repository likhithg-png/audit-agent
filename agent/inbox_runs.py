"""
Step 10 follow-up -- persistent status for the Gmail inbox tab (audit.inbox_runs).

Without this, GET /api/inbox/messages re-runs the Gmail search fresh every
call and a message already processed looks identical to one never touched.
This module is the memory: every message Gmail returns gets a row here,
and its status only ever moves forward through the one lifecycle:

    NEW -> PROCESSING -> NEEDS_REVIEW -> REPLIED
                      \-> ERROR

Classification (is_audit_request / confidence) is informational only -- it
is shown in the UI as a caption, never used to filter or hide a message. A
low-confidence email still shows up; a human decides whether to run it.

A hide-below-some-bar rule was tried twice and both times silently swallowed
a real request the human would have caught instantly: a forwarded audit
request scored 0.4919 (just under a 0.5 bar) and was hidden; after the bar
was lowered to 0.25, a plainly-intended request ("check for account
ACC100450 audit guideline has been passed or not") scored 0.1434 and was
hidden again. Hiding is strictly more dangerous than a wrong-looking caption
on a visible row, so list_saved() hides nothing -- not even at a lower bar.
Use the confidence score to decide AUTOSTART only (a much higher, separate
bar), never visibility.
"""
import re

from . import db
from .llm import call_decision


def sync_new_messages(candidates: list[dict]) -> None:
    """candidates: list_candidate_messages()'s output. Inserts a NEW row (with
    a Prism confidence score) for any message_id not already tracked; never
    touches a row that already exists -- status only moves forward elsewhere."""
    if not candidates:
        return
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT message_id FROM audit.inbox_runs WHERE message_id = ANY(%s)",
                    ([c["message_id"] for c in candidates],))
        known = {r["message_id"] for r in cur.fetchall()}
        for c in candidates:
            if c["message_id"] in known:
                continue
            is_audit_request, confidence = _classify(c["subject"], c["snippet"])
            cur.execute(
                """INSERT INTO audit.inbox_runs
                   (message_id, thread_id, subject, from_address, received_at,
                    status, is_audit_request, confidence, snippet, has_pdf)
                   VALUES (%s, %s, %s, %s, %s, 'NEW', %s, %s, %s, %s)
                   ON CONFLICT (message_id) DO NOTHING""",
                (c["message_id"], c.get("thread_id"), c["subject"], c["from"], c["date"],
                 is_audit_request, confidence, c.get("snippet", ""), bool(c.get("has_pdf"))),
            )
        conn.commit()


def known_ids() -> set:
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT message_id FROM audit.inbox_runs")
        return {r["message_id"] for r in cur.fetchall()}


def list_saved(own_address: str = "") -> list[dict]:
    """The inbox as last synced, newest first, straight from the database --
    no Gmail call. Mail sent from the connected mailbox itself is never listed.
    Everything else is listed, however low its confidence -- see the module
    docstring for why a hide-below-some-bar rule was tried twice and dropped
    both times."""
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM audit.inbox_runs ORDER BY created_at DESC, message_id")
        rows = cur.fetchall()
    out = []
    own = (own_address or "").lower()
    for r in rows:
        if own and own in (r["from_address"] or "").lower():
            continue
        out.append({
            "message_id": r["message_id"], "subject": r["subject"], "from": r["from_address"],
            "date": r["received_at"], "snippet": r["snippet"] or "", "has_pdf": bool(r["has_pdf"]),
            "status": r["status"], "is_audit_request": r["is_audit_request"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
            "request_id": r["request_id"],
        })
    return out


_FWD_SUBJECT = re.compile(r"^\s*(?:(?:fwd?|re|aw|tr)\s*:\s*)+", re.I)
_FWD_HEADER = re.compile(
    r"-{2,}\s*forwarded message\s*-{2,}|^\s*(?:from|sent|date|to|subject|cc)\s*:.*$|"
    r"^\s*on .{0,80}\bwrote:\s*$",
    re.I | re.M)


def _strip_forward_markers(subject: str, snippet: str) -> tuple:
    """Drop the "Fwd:" prefix and the quoted "---------- Forwarded message ----------
    From: ... To: ..." block before classifying.

    A forwarded audit request is still an audit request, but the wrapper is pure
    noise to the classifier: a real forwarded request in the connected mailbox
    scored 0.4919 against the auto-start bar that was in place at the time --
    close enough that the forwarding chrome around it was clearly dragging the
    score down. Classify what the email actually says, not the forwarding
    chrome around it."""
    return _FWD_SUBJECT.sub("", subject or "").strip(), _FWD_HEADER.sub(" ", snippet or "").strip()


# Acting WITHOUT a human (auto-start) still needs a bar -- unlike visibility, which
# needs none at all (see the module docstring for why list_saved() never hides on
# confidence alone). Set low (not "only the obvious ones") because the downstream
# tool-using decompose agent is itself a backstop: a low-confidence email that's
# actually too vague to run still gets caught there and surfaced as a clarifying
# question (see e.g. ACC100450 "has it passed or not" above), never silently
# mis-processed. AUTOSTART_AT only decides whether that happens right away or
# waits for a human to click Run.
AUTOSTART_AT = 0.1


def _classify(subject: str, snippet: str) -> tuple:
    """One Prism call: is this plausibly a real audit/compliance request?
    Never raises into the caller -- a classification hiccup should not stop
    the message from showing up in the list, just with no score attached."""
    subject, snippet = _strip_forward_markers(subject, snippet)
    try:
        r = call_decision(
            state=f'Email subject: "{subject}". Snippet: "{snippet}".',
            questions={"is_audit_request": {
                "type": "noul",
                "instructions": "True if this email is a genuine audit or compliance evidence "
                                 "request from a lender, false otherwise. A forwarded request "
                                 "is still a request.",
            }},
        )
        confidence = r["answers"]["is_audit_request"]["noul"]
        return confidence >= AUTOSTART_AT, confidence
    except Exception:
        return None, None


def list_statuses(message_ids: list[str]) -> dict:
    """message_id -> full row, for merging onto the live Gmail candidate list."""
    if not message_ids:
        return {}
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT * FROM audit.inbox_runs WHERE message_id = ANY(%s)", (message_ids,))
        return {r["message_id"]: r for r in cur.fetchall()}


def mark_processing(message_id: str, request_id: str) -> None:
    _update(message_id, status="PROCESSING", request_id=request_id, error=None)


def mark_needs_review(message_id: str) -> None:
    _update(message_id, status="NEEDS_REVIEW")


def mark_error(message_id: str, error: str) -> None:
    _update(message_id, status="ERROR", error=error)


def mark_replied(message_id: str) -> None:
    _update(message_id, status="REPLIED")


def _update(message_id: str, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k} = %s" for k in fields) + ", updated_at = now()"
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE audit.inbox_runs SET {cols} WHERE message_id = %s",
                    (*fields.values(), message_id))
        conn.commit()
