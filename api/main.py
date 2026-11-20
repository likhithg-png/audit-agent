"""
Step 9/10 -- FastAPI backend for the React frontend (replaces app.py / Streamlit).

Three ways to feed the pipeline, all converging on the same background-job
machinery (decompose -> run_checks -> pack -> letter), keyed by request_id:

  1. Sample requests       -- the 3 canned REQ-01/02/03 demo requests.
       GET  /api/requests
       GET  /api/requests/{id}/text
       POST /api/requests/{id}/run

  2. Upload your own        -- a PDF file or pasted text, plus which lender's
     sample to test against (the uploaded letter's content decides WHAT to
     check; the synthetic dataset only has real account data for 3 lenders,
     so that still has to be picked explicitly).
       GET  /api/lenders
       POST /api/custom/run

  3. Gmail inbox             -- a real mailbox, connected once via OAuth.
       GET  /api/inbox/status
       GET  /api/inbox/auth-url
       GET  /api/inbox/oauth/callback
       GET  /api/inbox/messages
       POST /api/inbox/messages/{message_id}/run
       POST /api/requests/{id}/send-reply    -- sends the (human-reviewed, editable) FINAL letter back
                                                 to the original sender. ONLY reachable via an explicit
                                                 button click in the UI -- this one never auto-sends.
                                                 The one exception to "nothing auto-sends": a run that
                                                 ends in needs_clarification auto-replies with the
                                                 question itself, no click required (_auto_reply_with_
                                                 clarification) -- it asserts nothing about the account,
                                                 it only asks for missing scope/period/severity-scale.

Shared, once a job is started under some request_id:
  GET /api/requests/{id}/status             -- poll: status + step log
  GET /api/requests/{id}/result              -- full bundle once status == "done" (includes
                                                 reply_to / replied for Gmail-sourced runs)
  GET /api/requests/{id}/gaps                -- gap_items.csv, optionally filtered
  GET /api/requests/{id}/verdicts            -- one row per account x check, ?status= EVIDENCED|GAP|CANNOT_DETERMINE|NOT_APPLICABLE
  GET /api/requests/{id}/download/{kind}     -- gap_report.xlsx (full detail)

A run is keyed by request_id, not a fresh job id -- out/<REQ>/ is already the
one-slot-per-request-id layout run_checks.py/pack.py/letter.py use, so
re-running a request simply overwrites its slot. Custom/Gmail runs get a
generated request_id (CUSTOM-xxxxxxxx / GMAIL-<message_id>) so they land in
their own out/ slot too, never colliding with the sample requests.
"""
import json
import threading
import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
import pandas as pd

from agent import gmail_client, inbox_runs, run_results, settings
from agent.config import DATA_DIR, FRONTEND_URL, OUT_DIR
from agent.decompose import decompose, decompose_from_guidelines
from agent.letter import draft_and_validate
from agent.pack import build_pack
from agent.run_checks import run
from agent.tools import Tools

app = FastAPI(title="Audit evidence pack agent")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SAMPLE_REQUESTS = {
    "REQ-01": dict(
        request_id="REQ-01", lender_id="L001", period={"from": "2026-01-01", "to": "2026-03-31"},
        sample_file="audit_requests/REQ-01_annexure_A_sample.csv", source="audit_requests/REQ-01_email.txt",
        label="REQ-01 -- plain email (L001)", path=DATA_DIR / "audit_requests/REQ-01_email.txt", kind="txt"),
    "REQ-02": dict(
        request_id="REQ-02", lender_id="L003", period={"from": "2026-01-01", "to": "2026-06-30"},
        sample_file="audit_requests/REQ-02_annexure_A_sample.csv", source="audit_requests/REQ-02_letter.pdf",
        label="REQ-02 -- PDF letter (L003)", path=DATA_DIR / "audit_requests/REQ-02_letter.pdf", kind="pdf"),
    "REQ-03": dict(
        request_id="REQ-03", lender_id="L002", period={"from": "2026-04-01", "to": "2026-06-30"},
        sample_file="audit_requests/REQ-03_annexure_A_sample.csv", source="audit_requests/REQ-03_email.txt",
        label="REQ-03 -- vague email (L002)", path=DATA_DIR / "audit_requests/REQ-03_email.txt", kind="txt"),
}

# Which lenders have real sampled-account data to test against -- independent
# of where the request TEXT comes from (sample picker / upload / Gmail).
LENDER_SAMPLES = {
    "L001": "audit_requests/REQ-01_annexure_A_sample.csv",
    "L002": "audit_requests/REQ-03_annexure_A_sample.csv",
    "L003": "audit_requests/REQ-02_annexure_A_sample.csv",
}

# in-memory job state, keyed by request_id -- one audit-agent process, matches
# the single out/<REQ>/ slot each request already uses. Guarded by a lock since
# the background run and status polling happen from different threads/tasks.
_lock = threading.Lock()
_jobs = {}  # request_id -> {"status": "idle"|"running"|"done"|"error", "log": [...], "error": str|None}


def _extract_text(path: Path, kind: str) -> str:
    if kind == "pdf":
        import pdfplumber
        with pdfplumber.open(path) as pdf:
            return "\n".join(p.extract_text() for p in pdf.pages)
    return path.read_text()


def _job(request_id: str) -> dict:
    with _lock:
        return _jobs.setdefault(request_id, {"status": "idle", "log": [], "error": None})


def _set(request_id: str, **kw):
    with _lock:
        _jobs[request_id].update(kw)


def _append_log(request_id: str, msg: str):
    with _lock:
        _jobs[request_id]["log"].append(msg)


def _describe_tool_call(t: dict) -> str:
    """One plain-English line for a tool call in the run log.

    The log is read by whoever is running the audit, not by a developer -- the
    old f"agent called {tool}({args})" form leaked Python dict reprs and raw SQL
    ({'query': "SELECT ... WHERE table_schema='audit'"}) straight into that view.
    The exact SQL is still kept in `agent_trace` in checks.json and shown in the
    UI's expandable trace, so nothing is hidden, just not shouted."""
    a = t.get("args") or {}
    tool = t.get("tool")
    if tool == "query_accounts":
        ids = a.get("account_ids") or []
        if not ids:
            return "Checked the account ids named in the request"
        if len(ids) == 1:
            return f"Checked account {ids[0]} exists"
        shown = ", ".join(ids[:3]) + (f" +{len(ids) - 3} more" if len(ids) > 3 else "")
        return f"Checked {len(ids)} account ids exist: {shown}"
    if tool == "lookup_policy":
        return f"Looked up {a.get('lender_id') or 'the lender'}'s policy rules"
    if tool == "list_guidelines":
        return "Looked up the guideline catalogue"
    if tool == "run_sql":
        return "Queried the audit data directly"
    if tool == "ask_clarifying_question":
        return "Stopped to ask for clarification instead of guessing"
    if tool == "_fell_back_to_one_shot":
        return "Investigation ran out of rounds -- used the one-shot extractor instead"
    return str(tool or "tool call")


AUTO_REPLY_SETTING_KEY = "auto_reply_clarification"


def _auto_reply_with_clarification(request_id: str, clarification: str) -> None:
    """A clarifying question is low-risk content -- it asserts nothing about the
    account, it only asks for the scope/period/severity-scale a human would
    otherwise have to type back manually. Sent automatically, unlike the final
    evidence-pack letter (api/main.py's module docstring), which always stays a
    manual, reviewed send -- that distinction is the point, not an oversight.
    Gated on AUTO_REPLY_SETTING_KEY (the toggle next to "Check for new mail"):
    off means a human reviews and sends it themselves, same as before this
    existed. No-op for non-Gmail runs (sample/upload have no originating
    email at all)."""
    if not request_id.startswith("GMAIL-"):
        return
    message_id = request_id.removeprefix("GMAIL-")
    if not settings.get_bool(AUTO_REPLY_SETTING_KEY, default=True):
        _append_log(request_id, "Auto-reply is turned off -- review and send this yourself.")
        inbox_runs.mark_needs_review(message_id)
        return
    reply_meta = _job(request_id).get("reply_meta") or run_results.reply_meta(request_id)
    if not reply_meta:
        inbox_runs.mark_error(message_id, "needs clarification, but has no originating email to reply to")
        return
    try:
        gmail_client.send_reply(
            to=reply_meta["from"], subject=reply_meta["subject"], body_text=clarification,
            thread_id=reply_meta.get("thread_id"), in_reply_to=reply_meta.get("message_id_header"),
            attachments=[],
        )
        _set(request_id, replied=True)
        run_results.mark_replied(request_id)
        inbox_runs.mark_replied(message_id)
        _append_log(request_id, f"Sent the clarifying question to {reply_meta['from']}")
    except Exception as e:
        _append_log(request_id, f"Could not send the clarifying question automatically: {e}")
        inbox_runs.mark_error(message_id, f"could not auto-send the clarifying question: {e}")


def _execute(request_id: str, text: str, lender_id: str, period: dict | None, sample_file: str, source: str,
             guideline_ids: list | None = None, recipient_name: str | None = None, use_agent: bool = True):
    """The one pipeline every entry point (sample picker / upload / Gmail)
    ultimately calls, just with a different source for `text`.

    use_agent: default True as of Step 14 -- eval/eval_decompose_agent.py
    measured decompose_with_tools() as identical to the one-shot baseline on
    REQ-01/02/03 (same gap recall, false-gap rate, severity/status accuracy),
    so the tool-using path is now what the sample picker / upload / Gmail
    entry points all use unless a caller explicitly opts out with
    use_agent=False. Only applies when there's request text to investigate --
    a guideline-only run has no ambiguity to resolve, so it always uses
    decompose_from_guidelines regardless of this flag."""
    try:
        _append_log(request_id, "Decomposing the request...")
        if text and text.strip():
            if use_agent:
                _append_log(request_id, "Using the tool-using agent (may look up guidelines/policy/accounts "
                                         "before committing to checks)...")
                from agent.decompose_agent import decompose_with_tools
                decomposed = decompose_with_tools(request_text=text, request_id=request_id, lender_id=lender_id,
                                                  period=period, sample_file=sample_file, source=source)
                if "needs_clarification" in decomposed:
                    for t in decomposed.get("agent_trace", []):
                        _append_log(request_id, f"  {_describe_tool_call(t)}")
                    # The full question goes in its own field, not into the error string:
                    # it used to be pasted into both the log line and the raised error, so
                    # a paragraph-long question rendered twice in red.
                    clarification = decomposed["needs_clarification"]
                    _set(request_id, clarification=clarification, status="error",
                         error="The agent needs more information before it can run this request.")
                    _append_log(request_id, "Error: The agent needs more information before it can run this request.")
                    _auto_reply_with_clarification(request_id, clarification)
                    return
                # kept in `decomposed` (not popped) -- it's written into checks.json below,
                # so the investigation trail survives the run, not just these log lines
                for t in decomposed.get("agent_trace", []):
                    _append_log(request_id, f"  {_describe_tool_call(t)}")
            else:
                decomposed = decompose(request_text=text, request_id=request_id, lender_id=lender_id,
                                        period=period, sample_file=sample_file, source=source)
            if guideline_ids:
                _append_log(request_id, f"Adding {len(guideline_ids)} selected guideline(s) to the "
                                         f"{len(decomposed['checks'])} check(s) found in the request")
                decomposed = decompose_from_guidelines(guideline_ids, request_id, lender_id, period, sample_file,
                                                       source, base=decomposed)
        else:
            _append_log(request_id, "No request text -- using the selected guidelines")
            decomposed = decompose_from_guidelines(guideline_ids or [], request_id, lender_id, period, sample_file, source)
        decomposed_path = OUT_DIR / request_id / "checks.json"
        decomposed_path.parent.mkdir(parents=True, exist_ok=True)
        decomposed_path.write_text(json.dumps(decomposed, indent=2))
        _append_log(request_id, f"Extracted {len(decomposed['checks'])} checks, "
                                 f"{len(decomposed['unsupported'])} unsupported")
        # With no checks there is nothing to run, and run_checks' empty pivot used to
        # surface as a bare KeyError ("Error: 'account_id'") that said nothing useful.
        if not decomposed["checks"]:
            unsupported = decomposed.get("unsupported") or []
            detail = (f" The request mentioned {len(unsupported)} requirement(s) that don't map to a check "
                      f"this agent can run." if unsupported else "")
            raise ValueError(
                "No runnable checks could be read from this request." + detail +
                " Name the checks explicitly (e.g. 'calling hours, call frequency, WhatsApp consent'), "
                "or pick them from the Guidelines list.")
        pr = decomposed["period"]
        if decomposed["period_source"] == "extracted_relative":
            # Resolved from relative wording ("last 2 months") against the real run date --
            # say so plainly; re-running this same text later will legitimately shift the
            # window, and that must never look like a silent inconsistency.
            period_note = pr.get("note", "resolved relative to the run date")
            _append_log(request_id, f"Audit period: {pr['from']} to {pr['to']} ({period_note})")
        else:
            _append_log(request_id, f"Audit period: {pr['from']} to {pr['to']} "
                                     f"({'from the request' if decomposed['period_source'] == 'extracted' else 'as entered'})")

        _append_log(request_id, "Running checks against the sample...")
        vdf = run(decomposed_path, tools=Tools(), verbose=False)
        _append_log(request_id, f"Tested {vdf.account_id.nunique()} accounts")

        _append_log(request_id, "Building the evidence pack...")
        pack_paths = build_pack(request_id, decomposed_path)

        _append_log(request_id, "Drafting the response letter...")
        letter_result = draft_and_validate(request_id, decomposed_path, recipient_name=recipient_name)

        with _lock:
            _jobs[request_id]["result"] = dict(
                request_id=request_id, decomposed=decomposed, pack_paths=pack_paths,
                letter=letter_result,
            )
        try:  # survives an API restart; never let a persistence hiccup fail the run
            run_results.save_letter(request_id, letter_result)
        except Exception as e:
            print(f"  warning: could not persist the letter for {request_id} ({e})")
        _append_log(request_id, "Done")
        _set(request_id, status="done")
        if request_id.startswith("GMAIL-"):
            inbox_runs.mark_needs_review(request_id.removeprefix("GMAIL-"))
    except Exception as e:
        _set(request_id, status="error", error=str(e))
        _append_log(request_id, f"Error: {e}")
        if request_id.startswith("GMAIL-"):
            inbox_runs.mark_error(request_id.removeprefix("GMAIL-"), str(e))


def _resolve_sample(request_id: str, lender_id: str | None, annexure_bytes: bytes | None, annexure_name: str | None,
                    request_text: str | None = None):
    """Decide which accounts to test. Returns (lender_id, sample_file).

    - Annexure A attached (csv/xlsx with an account_id column): use exactly
      those accounts. Every id is checked against audit.accounts; the lender
      is inferred from them if not given, and must match if given (lender
      policy overrides in D12 depend on it).
    - Nothing attached: fall back to the chosen lender's built-in sample.
    """
    import io
    import re
    name = (annexure_name or "").lower()
    ids_from_text: list[str] = []
    if not annexure_bytes:
        # No account list: use any account ids the request itself names.
        ids_from_text = list(dict.fromkeys(re.findall(r"\bACC\d+\b", request_text or "", flags=re.I)))
        ids_from_text = [a.upper() for a in ids_from_text]
        if not ids_from_text:
            if lender_id in LENDER_SAMPLES:
                return lender_id, LENDER_SAMPLES[lender_id]
            raise HTTPException(400, "no accounts to test -- attach an account list, or name the account ids "
                                     "(e.g. ACC100001) in the request")
    try:
        if ids_from_text:
            df = pd.DataFrame({"account_id": ids_from_text})
        elif name.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(annexure_bytes), dtype=str)
        elif name.endswith((".xlsx", ".xls")):
            df = pd.read_excel(io.BytesIO(annexure_bytes), dtype=str)
        else:
            raise HTTPException(400, "account list must be a .csv or .xlsx file")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, f"could not read the account list: {e}")
    cols = {str(c).strip().lower(): c for c in df.columns}
    col = cols.get("account_id") or cols.get("account id") or cols.get("accountid")
    if col is None:
        if len(df.columns) == 1:
            col = df.columns[0]
        else:
            raise HTTPException(400, f"account list needs an 'account_id' column (found: {list(df.columns)})")
    ids = list(dict.fromkeys(a.strip() for a in df[col].dropna().astype(str) if a.strip()))
    if not ids:
        raise HTTPException(400, "the account list has no account ids")

    from agent.db import connect
    with connect() as conn:
        rows = conn.execute("SELECT account_id, lender_id FROM audit.accounts WHERE account_id = ANY(%s)", (ids,)).fetchall()
    found = {r["account_id"]: r["lender_id"] for r in rows}
    missing = [a for a in ids if a not in found]
    if missing:
        raise HTTPException(400, f"{len(missing)} of {len(ids)} account ids are not in the dataset, e.g. {missing[:5]}")
    lenders = sorted(set(found.values()))
    if lender_id:
        bad = [a for a, l in found.items() if l != lender_id]
        if bad:
            raise HTTPException(400, f"{len(bad)} accounts do not belong to lender {lender_id}, e.g. {bad[:5]}")
    elif len(lenders) == 1:
        lender_id = lenders[0]
    else:
        raise HTTPException(400, f"the accounts span several lenders {lenders} -- pick one lender or split the list")

    out = OUT_DIR / request_id
    out.mkdir(parents=True, exist_ok=True)
    path = out / "annexure_a.csv"
    pd.DataFrame({"sr_no": range(1, len(ids) + 1), "account_id": ids}).to_csv(path, index=False)
    return lender_id, str(path)  # absolute: DATA_DIR / abs == abs


def _period_arg(period_from: str | None, period_to: str | None) -> dict | None:
    """Both dates, or neither (then decompose() reads the period from the request)."""
    from datetime import date
    period_from, period_to = (period_from or "").strip(), (period_to or "").strip()
    if not period_from and not period_to:
        return None
    if not (period_from and period_to):
        raise HTTPException(400, "enter both 'Period from' and 'Period to', or leave both blank")
    try:
        f, t = date.fromisoformat(period_from), date.fromisoformat(period_to)
    except ValueError:
        raise HTTPException(400, "period dates must be YYYY-MM-DD")
    if f > t:
        raise HTTPException(400, "'Period from' is after 'Period to'")
    return {"from": period_from, "to": period_to}


def _start(request_id: str, background_tasks: BackgroundTasks, **kw):
    job = _job(request_id)
    if job["status"] == "running":
        raise HTTPException(409, "a run for this request is already in progress")
    _set(request_id, status="running", log=[], error=None)
    background_tasks.add_task(_execute, request_id, **kw)


# ---------------------------------------------------------------- 1. sample requests

@app.get("/api/requests")
def list_requests():
    return [{"request_id": r["request_id"], "label": r["label"], "lender_id": r["lender_id"],
              "period": r["period"]} for r in SAMPLE_REQUESTS.values()]


@app.get("/api/requests/{request_id}/text")
def request_text(request_id: str):
    job = SAMPLE_REQUESTS.get(request_id)
    if not job:
        raise HTTPException(404, "unknown request_id")
    return {"text": _extract_text(job["path"], job["kind"])}


@app.post("/api/requests/{request_id}/run", status_code=202)
def start_sample_run(request_id: str, background_tasks: BackgroundTasks):
    job = SAMPLE_REQUESTS.get(request_id)
    if not job:
        raise HTTPException(404, "unknown request_id")
    text = _extract_text(job["path"], job["kind"])
    _start(request_id, background_tasks, text=text, lender_id=job["lender_id"], period=job["period"],
           sample_file=job["sample_file"], source=job["source"])
    return {"request_id": request_id, "status": "running"}


# ---------------------------------------------------------------- 2. upload your own

@app.get("/api/guidelines")
def list_guidelines():
    from agent.guidelines import list_guidelines as _lg
    return _lg()


@app.get("/api/lenders")
def list_lenders():
    return [{"lender_id": lid, "sample_file": sf} for lid, sf in LENDER_SAMPLES.items()]


@app.post("/api/custom/run", status_code=202)
async def start_custom_run(
    background_tasks: BackgroundTasks,
    period_from: str | None = Form(None),
    period_to: str | None = Form(None),
    lender_id: str | None = Form(None),
    text: str | None = Form(None),
    guideline_ids: str | None = Form(None),   # comma-separated; used when the request names no checks
    use_agent: bool = Form(True),              # default on since Step 14 -- see _execute() docstring; the
                                                # UploadPane checkbox now opts OUT ("skip investigation")
    file: UploadFile | None = File(None),
    accounts_file: UploadFile | None = File(None),
):
    gids = [g.strip() for g in (guideline_ids or "").split(",") if g.strip()]
    if not file and not (text and text.strip()) and not gids:
        raise HTTPException(400, "provide a request file, pasted text, or select guidelines to check")
    if gids:
        from agent.guidelines import checks_for
        try:
            checks_for(gids)
        except ValueError as e:
            raise HTTPException(400, str(e))

    if file:
        from agent.file_text import extract_text
        try:
            request_text_ = extract_text(file.filename, await file.read())
        except ValueError as e:
            raise HTTPException(400, str(e))
        source = f"upload:{file.filename}"
    else:
        request_text_ = text or ""
        source = "upload:pasted-text" if request_text_.strip() else "upload:selected-guidelines"

    request_id = f"CUSTOM-{uuid.uuid4().hex[:8]}"
    ann = await accounts_file.read() if accounts_file else None
    lender_id, sample_file = _resolve_sample(request_id, lender_id or None, ann, accounts_file.filename if accounts_file else None, request_text_)
    _start(request_id, background_tasks, text=request_text_, lender_id=lender_id,
           period=_period_arg(period_from, period_to), sample_file=sample_file, source=source,
           guideline_ids=gids, use_agent=use_agent)
    return {"request_id": request_id, "status": "running"}


# ---------------------------------------------------------------- 3. Gmail inbox

@app.get("/api/inbox/status")
def inbox_status():
    if not gmail_client.is_configured():
        return {"configured": False, "connected": False}
    return {"configured": True, "connected": gmail_client.is_connected()}


@app.get("/api/inbox/auto-reply")
def get_auto_reply_setting():
    return {"enabled": settings.get_bool(AUTO_REPLY_SETTING_KEY, default=True)}


@app.post("/api/inbox/auto-reply")
def set_auto_reply_setting(enabled: bool = Form(...)):
    settings.set_bool(AUTO_REPLY_SETTING_KEY, enabled)
    return {"enabled": enabled}


@app.get("/api/inbox/auth-url")
def inbox_auth_url():
    if not gmail_client.is_configured():
        raise HTTPException(409, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set -- see README.md")
    return {"url": gmail_client.build_auth_url()}


@app.get("/api/inbox/oauth/callback")
def inbox_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"{FRONTEND_URL}/?inbox_error={error}")
    try:
        gmail_client.exchange_code(code, state)
    except Exception as e:
        return RedirectResponse(f"{FRONTEND_URL}/?inbox_error={e}")
    return RedirectResponse(f"{FRONTEND_URL}/?inbox=connected")


def _begin_gmail_run(message_id: str, background_tasks: BackgroundTasks, lender_id: str | None = None,
                     period_from: str | None = None, period_to: str | None = None,
                     accounts_bytes: bytes | None = None, accounts_name: str | None = None,
                     text: str | None = None) -> str:
    """Shared by the manual 'Run agent on this email' button and the
    auto-start path below -- same pipeline either way."""
    request_id = f"GMAIL-{message_id}"
    if text is None:
        text = gmail_client.get_message_text(message_id)
    # A real audit request usually attaches its Annexure A rather than listing ids inline.
    # Nothing in the Gmail path used to look for it, so those requests had no accounts at all.
    if accounts_bytes is None:
        try:
            found = gmail_client.find_account_list(message_id)
        except Exception:
            found = None
        if found:
            accounts_name, accounts_bytes = found
    lender_id, sample_file = _resolve_sample(request_id, lender_id, accounts_bytes, accounts_name, text)
    # fetched fresh from Gmail, not trusted from the client -- this is who
    # /send-reply will actually send to later, once a human approves it.
    reply_meta = gmail_client.get_message_meta(message_id)
    _start(request_id, background_tasks, text=text, lender_id=lender_id,
           period=_period_arg(period_from, period_to), sample_file=sample_file,
           source=f"gmail:{message_id}", recipient_name=reply_meta.get("from_name") or None)
    _set(request_id, reply_meta=reply_meta, replied=False)
    run_results.save_reply_meta(request_id, reply_meta)
    run_results.reset_replied(request_id)
    inbox_runs.mark_processing(message_id, request_id)
    return request_id


def _try_autostart(message_id: str, background_tasks: BackgroundTasks) -> bool:
    """Auto-starts a run with no human click -- but ONLY when the email actually
    carries something to test against: either the body names account ids
    (\\bACC\\d+\\b) or it attaches an Annexure A spreadsheet. There's no human in
    this path to supply an account list, so an email with neither stays NEW for
    manual handling rather than auto-starting a run that can only fail.

    This gate is deliberately NOT just "Prism said it's an audit request":
    Prism scores whether the PROSE reads like a real request, which says nothing
    about whether the accounts to test are reachable. Dropping it would turn
    "quietly not started" into a run that errors out on 'no accounts to test'.
    Never lets one bad auto-start attempt break the whole /messages response."""
    import re
    try:
        text = gmail_client.get_message_text(message_id)
    except Exception:
        return False
    if not re.search(r"\bACC\d+\b", text, re.I):
        try:
            if gmail_client.find_account_list(message_id) is None:
                return False
        except Exception:
            return False
    try:
        _begin_gmail_run(message_id, background_tasks, text=text)
        return True
    except Exception:
        return False


@app.get("/api/inbox/messages")
def inbox_messages(background_tasks: BackgroundTasks, sync: bool = False):
    """sync=false (default): the list as saved in the database -- no Gmail call.
    sync=true: look in Gmail for mail not seen before, save it, auto-start runs
    for ones that name account ids, then return the saved list."""
    if not gmail_client.is_connected():
        raise HTTPException(409, "Gmail is not connected -- call /api/inbox/auth-url first")
    if sync:
        new = gmail_client.list_candidate_messages(skip_ids=inbox_runs.known_ids())
        inbox_runs.sync_new_messages(new)
        for c in new:
            row = inbox_runs.list_statuses([c["message_id"]]).get(c["message_id"], {})
            if row.get("is_audit_request"):
                _try_autostart(c["message_id"], background_tasks)
    return inbox_runs.list_saved(gmail_client.own_address())


@app.post("/api/inbox/messages/{message_id}/run", status_code=202)
async def start_inbox_run(message_id: str, background_tasks: BackgroundTasks,
                           period_from: str | None = Form(None), period_to: str | None = Form(None),
                           lender_id: str | None = Form(None), accounts_file: UploadFile | None = File(None)):
    ann = await accounts_file.read() if accounts_file else None
    request_id = _begin_gmail_run(message_id, background_tasks, lender_id or None, period_from, period_to,
                                  ann, accounts_file.filename if accounts_file else None)
    return {"request_id": request_id, "status": "running"}


@app.post("/api/requests/{request_id}/send-reply")
def send_reply(request_id: str, body: str = Form(...), attach: str | None = Form(None)):
    """Sends the (possibly human-edited) letter back to the original sender.
    Only reachable for a Gmail-sourced run -- sample/upload runs have no real
    inbox message to reply into. Nothing else in this app calls this; it only
    runs when a human in the UI clicks Send.

    attach: comma-separated keys from _DOWNLOAD_FILES (just gap_report) -- the same evidence-pack file the UI
    offers as downloads, attached to the reply. The human picks them; nothing
    is attached by default."""
    job = _job(request_id)
    persisted = run_results.load(request_id) or {}
    # in-memory first, then the persisted copy -- a finished run must stay
    # replyable across an API restart, not just within one process lifetime
    reply_meta = job.get("reply_meta") or run_results.reply_meta(request_id)
    if not reply_meta:
        raise HTTPException(400, "this request has no originating email to reply to")
    if job.get("replied") or persisted.get("replied"):
        raise HTTPException(409, "a reply has already been sent for this request")

    paths = []
    for kind in (k.strip() for k in (attach or "").split(",") if k.strip()):
        if kind not in _DOWNLOAD_FILES:
            raise HTTPException(400, f"unknown attachment {kind!r}")
        filename, _ = _DOWNLOAD_FILES[kind]
        path = OUT_DIR / request_id / filename
        if not path.exists():
            raise HTTPException(409, f"{filename} has not been generated for this request")
        paths.append(path)

    sent_message_id = gmail_client.send_reply(
        to=reply_meta["from"], subject=reply_meta["subject"], body_text=body,
        thread_id=reply_meta.get("thread_id"), in_reply_to=reply_meta.get("message_id_header"),
        attachments=paths,
    )
    _set(request_id, replied=True)
    run_results.mark_replied(request_id)
    if request_id.startswith("GMAIL-"):
        inbox_runs.mark_replied(request_id.removeprefix("GMAIL-"))
    return {"sent": True, "to": reply_meta["from"], "message_id": sent_message_id}


# ---------------------------------------------------------------- shared: status/result/gaps/downloads

@app.get("/api/requests/{request_id}/status")
def run_status(request_id: str):
    job = _job(request_id)
    # `clarification` is NOT a failure in the usual sense -- the agent investigated and
    # decided it would rather ask than guess (ask_clarifying_question). It's surfaced
    # separately so the UI can present it as a question to answer, not a red error.
    # `reply_meta` is captured at the START of every Gmail run (_begin_gmail_run), before
    # decompose even runs, so it's already here even on a clarification/error outcome --
    # that's what lets the UI offer to send the clarifying question straight back to the
    # sender instead of making a human leave the app and retype it into their own client.
    reply_meta = job.get("reply_meta") or run_results.reply_meta(request_id)
    # in-memory first, then persisted -- same reasoning as send_reply()'s own check:
    # "already sent" must survive an API restart, not just this process's lifetime.
    persisted = run_results.load(request_id) or {}
    return {"status": job["status"], "log": job["log"], "error": job["error"],
            "clarification": job.get("clarification"), "reply_to": (reply_meta or {}).get("from"),
            "replied": job.get("replied", False) or bool(persisted.get("replied"))}


@app.get("/api/requests/{request_id}/result")
def run_result(request_id: str):
    job = _job(request_id)
    od = OUT_DIR / request_id
    persisted = run_results.load(request_id)

    if job["status"] == "done":
        decomposed = job["result"]["decomposed"]
        letter = job["result"]["letter"]
        reply_meta, replied = job.get("reply_meta"), job.get("replied", False)
    elif job["status"] in ("error", "running"):
        # Never fall through to the persisted copy when we KNOW this run failed or is
        # still going. A request_id is a stable slot (GMAIL-<id> especially, via "Run
        # again"), so a failed re-run would otherwise serve the PREVIOUS run's letter
        # as if it were this one's -- while checks.json on disk has already been
        # overwritten by the failed run. That mix renders as new checks beside an old
        # letter, with Send reply enabled: a reviewer could mail a lender a letter that
        # does not describe the run they are looking at.
        raise HTTPException(409, f"run is not done (status={job['status']})")
    elif persisted and persisted.get("letter_text") and (od / "checks.json").exists():
        # The API restarted since this run finished -- in-memory _jobs is gone,
        # but everything needed is on disk + in audit.run_results. Without this
        # a Gmail message stuck at NEEDS_REVIEW could never be reviewed or
        # replied to after a redeploy.
        decomposed = json.loads((od / "checks.json").read_text())
        letter = {"text": persisted["letter_text"],
                  "needs_human_review": persisted["needs_human_review"],
                  "validation": persisted["validation"] or {"passed": True, "reasons": []}}
        reply_meta = run_results.reply_meta(request_id)
        replied = persisted["replied"]
    else:
        raise HTTPException(409, f"run is not done (status={job['status']})")

    summary = pd.read_csv(od / "summary.csv").to_dict("records")
    from agent.explain import check_label
    labels = {c["check_id"]: check_label(c) for c in decomposed["checks"]}
    for row in summary:
        row["check"] = labels.get(row.get("check_id"), row.get("check_type"))
    checks_out = [dict(c, check_label=check_label(c)) for c in decomposed["checks"]]

    # Scorecard only exists for REQ-01/02/03 -- they're the only requests with ground truth.
    # Scoped to THIS request_id specifically: eval.score.score()'s "overall" is a blend across
    # every out/REQ-*/ on disk, which previously got shown unchanged on every run's result page
    # (custom uploads, Gmail runs included) -- meaningless for those, and even for REQ-01/02/03
    # it was never actually that request's own number. Rebuilt here from per_check's per-check
    # counts (true_gaps/found/false_gaps/unsupported_claims), summed for just this request_id.
    score = None
    if request_id in SAMPLE_REQUESTS:
        try:
            from eval.score import score as run_score
            _, per_check, _ = run_score(verbose=False)
            if request_id in per_check.index.get_level_values(0):
                rid_rows = per_check.loc[request_id]
                tg, found, fp = rid_rows["true_gaps"].sum(), rid_rows["found"].sum(), rid_rows["false_gaps"].sum()
                pg = found + fp
                score = {
                    "recall": float(found / tg) if tg else 1.0,
                    "false_gap_rate": float(fp / pg) if pg else 0.0,
                    "unsupported_claims": int(rid_rows["unsupported_claims"].sum()),
                    "per_check": rid_rows.reset_index().to_dict("records"),
                }
        except Exception as e:
            score = {"error": str(e)}

    return {
        "request_id": request_id,
        "decomposed": {"checks": checks_out, "unsupported": decomposed["unsupported"],
                       "agent_trace": decomposed.get("agent_trace", [])},
        "summary": summary,
        "letter": {"text": letter["text"], "needs_human_review": letter["needs_human_review"],
                   "validation": letter["validation"]},
        "score": score,
        "reply_to": reply_meta["from"] if reply_meta else None,
        "replied": replied,
    }


@app.get("/api/requests/{request_id}/gaps")
def gaps(request_id: str, check_type: str | None = None, severity: str | None = None):
    od = OUT_DIR / request_id
    path = od / "gap_items.csv"
    if not path.exists():
        raise HTTPException(409, "no run output yet for this request")
    df = pd.read_csv(path, dtype=str)
    if check_type:
        df = df[df.check_type == check_type]
    if severity:
        df = df[df.severity == severity]
    return df.to_dict("records")


@app.get("/api/requests/{request_id}/verdicts")
def verdicts(request_id: str, status: str | None = None, check_type: str | None = None):
    """One row per account x check (verdicts.csv). status: EVIDENCED | GAP | CANNOT_DETERMINE | NOT_APPLICABLE."""
    path = OUT_DIR / request_id / "verdicts.csv"
    if not path.exists():
        raise HTTPException(409, "no run output yet for this request")
    if status and status not in ("EVIDENCED", "GAP", "CANNOT_DETERMINE", "NOT_APPLICABLE"):
        raise HTTPException(400, "status must be EVIDENCED, GAP, CANNOT_DETERMINE or NOT_APPLICABLE")
    df = pd.read_csv(path, dtype=str).fillna("")
    if status:
        df = df[df.status == status]
    if check_type:
        df = df[df.check_type == check_type]
    return df.to_dict("records")


@app.get("/api/requests/{request_id}/account-results")
def account_results(request_id: str):
    """One row per account x check with channel, plain-English reason and the underlying records."""
    if not (OUT_DIR / request_id / "verdicts.csv").exists():
        raise HTTPException(409, "no run output yet for this request")
    from agent.explain import account_results as _ar
    return _ar(request_id)


_DOWNLOAD_FILES = {
    "gap_report": ("gap_report.xlsx",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
}


@app.get("/api/requests/{request_id}/download/{kind}")
def download(request_id: str, kind: str):
    if kind not in _DOWNLOAD_FILES:
        raise HTTPException(404, "unknown download kind")
    filename, media_type = _DOWNLOAD_FILES[kind]
    path = OUT_DIR / request_id / filename
    if not path.exists():
        raise HTTPException(409, "no run output yet for this request")
    return FileResponse(path, media_type=media_type, filename=f"{request_id}_{filename}")
