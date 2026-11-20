"""
Step 13 -- a genuinely tool-using decompose: the model can investigate the
guideline catalogue, a lender's real policy, and the data itself before
committing to a checks.json, instead of extracting from text alone in one shot.

This is additive, not a replacement. agent/decompose.py's one-shot decompose()
is untouched and stays the default everywhere -- this only runs when a caller
explicitly opts in (the "Use agentic decompose" toggle). The output goes
through the EXACT SAME validation decompose() already has: the closed
CHECK_TYPES catalogue, the verbatim-quote check, and the severity-defaults
backstop. Nothing the agent "decides" bypasses what already catches bad output.

Native tool-calling confirmed live against Sarvam/Deepseek before building
this (standard OpenAI tool_calls shape, not guessed) -- see STATUS.md.
"""
import json

from . import db
from .decompose import (
    CHECK_TYPES,
    Decomposition,
    SYSTEM_PROMPT as BASE_SYSTEM_PROMPT,
    _fill_severity_defaults,
    _normalize_ws,
    _resolve_period,
    _unquote,
    decompose,
)
from .guidelines import list_guidelines
from .llm import call_with_tools, extract_json
from .tools import Tools

MAX_STEPS = 6  # tool-call rounds before giving up rather than looping forever

TOOLS = [
    {"type": "function", "function": {
        "name": "list_guidelines",
        "description": "List the RBI/lender guideline catalogue -- each entry already maps to a real "
                       "check_type. Use this to recognize when the request's intent matches an existing "
                       "guideline even if the wording is different (e.g. a vague mention of 'calling hours').",
        "parameters": {"type": "object", "properties": {}, "required": []},
    }},
    {"type": "function", "function": {
        "name": "lookup_policy",
        "description": "The real policy rules (calling-hours window, call-frequency caps, notice deadlines) "
                       "for one lender -- check this before guessing a severity scale the request didn't state.",
        "parameters": {"type": "object",
                       "properties": {"lender_id": {"type": "string", "description": "e.g. L001"}},
                       "required": ["lender_id"]},
    }},
    {"type": "function", "function": {
        "name": "query_accounts",
        "description": "Check whether account ids mentioned in the request actually exist and which lender "
                       "they belong to, before committing to them.",
        "parameters": {"type": "object",
                       "properties": {"account_ids": {"type": "array", "items": {"type": "string"}}},
                       "required": ["account_ids"]},
    }},
    {"type": "function", "function": {
        "name": "run_sql",
        "description": "Read-only SELECT against the audit schema, for anything the other tools don't cover "
                       "(e.g. 'does this lender have any device-financing accounts'). Tables: audit.accounts, "
                       "audit.contact_log, audit.consent, audit.templates, audit.policy, audit.notices, "
                       "audit.certificates, audit.guidelines.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "ask_clarifying_question",
        "description": "Call this INSTEAD OF producing a final checks.json when the request is genuinely "
                       "ambiguous even after investigating -- e.g. it names a product or requirement that "
                       "doesn't exist in any table. Ends the run and surfaces your question to a human "
                       "instead of guessing at a checks.json.",
        "parameters": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    }},
]

SYSTEM_PROMPT = """You have tools to investigate before committing to a final checks.json -- use
them when the request is vague, names something you're not sure exists, or doesn't state
its own severity scale. Don't call a tool just to call it: if the request already states
everything needed, go straight to the final JSON. Call ask_clarifying_question INSTEAD OF
the final JSON if, after investigating, the request still doesn't map to anything
testable -- never guess at that point.

""" + BASE_SYSTEM_PROMPT


def _run_tool(name: str, args: dict, tools: Tools) -> str:
    if name == "list_guidelines":
        return json.dumps(list_guidelines())
    if name == "lookup_policy":
        df = tools.policy()
        rows = df[df.lender_id.isin([args["lender_id"], "ALL"])]
        return rows.to_json(orient="records")
    if name == "query_accounts":
        ids = args.get("account_ids", [])
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT account_id, lender_id FROM audit.accounts WHERE account_id = ANY(%s)", (ids,)
            ).fetchall()
        found = {r["account_id"] for r in rows}
        return json.dumps({"found": rows, "not_found": [a for a in ids if a not in found]})
    if name == "run_sql":
        try:
            df = tools.sql(args["query"])
        except Exception as e:
            return json.dumps({"error": str(e)})
        return df.head(50).to_json(orient="records")
    raise ValueError(f"unknown tool {name!r}")


def _known_context(lender_id: str, period, sample_file: str) -> str:
    """What _resolve_sample() / the caller already nailed down BEFORE this agent
    ever sees the request -- an attached Annexure A CSV, account ids already
    named in the text, or dates already entered in the UI. Without this, the
    agent reasons from request_text alone and has no way to know those were
    already resolved: a real run with a 42-account CSV attached still got
    "no attachment or account list came through, no lender is named" asked
    back at the sender, even though both were already fixed by the time
    decompose_with_tools ran. Told explicitly so it only asks about what is
    ACTUALLY still missing (guideline scope, a genuinely unstated period)."""
    lines = []
    if lender_id:
        lines.append(f"- lender_id is already fixed for this run: {lender_id}. Do not ask which lender it is.")
    if sample_file:
        lines.append("- The account list to test is already fixed for this run (from an attached file, or "
                     "account ids already named in the request). Do not ask for an account list or an "
                     "attachment -- one already exists, whether or not the request text itself describes it.")
    if period and period.get("from") and period.get("to"):
        lines.append(f"- The audit period is already fixed: {period['from']} to {period['to']}. Do not ask for dates.")
    else:
        lines.append("- No period has been entered manually. If the request states ANY period at all -- including "
                     "relative wording like \"last 2 months\" or \"last 3 months including this month\" -- the "
                     "system resolves it automatically against the real run date; do not ask for a fixed period "
                     "just because the wording is relative. Only ask about the period if the request states no "
                     "period-like phrase whatsoever, or uses a unit the resolver doesn't cover (e.g. \"last quarter\").")
    return "\n".join(lines)


def decompose_with_tools(request_text: str, request_id: str, lender_id: str, period, sample_file: str,
                         source: str = "") -> dict:
    """Same output shape as decompose(), plus "agent_trace": every tool call it
    actually made, so a human can see exactly what it looked at before
    deciding -- that trail is the point of making this agentic, not just a
    side effect. Returns {"needs_clarification": ..., "agent_trace": [...]}
    instead of a checks.json if the model calls ask_clarifying_question.
    Raises the same way decompose() does if the final output doesn't validate,
    or if it runs MAX_STEPS tool-call rounds without ever producing one."""
    tools_instance = Tools()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "Known context for this specific run, already resolved by the pipeline "
                                      "before you were called:\n" + _known_context(lender_id, period, sample_file)},
        {"role": "user", "content": request_text},
    ]
    trace = []
    raw = None

    for _ in range(MAX_STEPS):
        msg, _finish_reason = call_with_tools(messages, TOOLS)
        messages.append({"role": "assistant", "content": msg.get("content"), "tool_calls": msg.get("tool_calls")})

        if not msg.get("tool_calls"):
            raw = msg.get("content") or ""
            break

        clarification = None
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            args = json.loads(call["function"]["arguments"] or "{}")
            if name == "ask_clarifying_question":
                trace.append({"tool": name, "args": args})
                clarification = args.get("question", "")
                break
            result = _run_tool(name, args, tools_instance)
            trace.append({"tool": name, "args": args, "result_preview": str(result)[:300]})
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        if clarification is not None:
            return {"needs_clarification": clarification, "agent_trace": trace}
    else:
        # The model used every round on tool calls and never committed to a final
        # checks.json. Reproducible, not flaky: a vague request ("do all checks")
        # combined with a span period sent it exploring information_schema instead
        # of answering, three runs out of three. Raising here failed the whole run
        # on the DEFAULT path (Step 14) for a request the proven one-shot
        # decompose() handles correctly, so fall back to it rather than lose the
        # run. The fallback is recorded in the trace, never silent.
        result = decompose(request_text=request_text, request_id=request_id, lender_id=lender_id,
                           period=period, sample_file=sample_file, source=source)
        trace.append({"tool": "_fell_back_to_one_shot", "args": {},
                      "result_preview": f"The agent used all {MAX_STEPS} investigation rounds without "
                                        f"committing to an answer, so the one-shot extractor produced "
                                        f"these checks instead."})
        result["agent_trace"] = trace
        return result

    parsed = Decomposition.model_validate(extract_json(raw))

    normalized_request = _normalize_ws(request_text)
    checks, unsupported = [], []
    for c in parsed.checks:
        c.source_text = _unquote(c.source_text)   # model often returns the span already quoted
        verbatim = _normalize_ws(c.source_text) in normalized_request
        if not verbatim:
            c.test = f"[UNVERIFIED QUOTE -- source_text not found verbatim] {c.test}"
        if c.check_type in CHECK_TYPES and verbatim:
            checks.append(c.model_dump())
        else:
            unsupported.append(c.model_dump())

    period, period_source = _resolve_period(period, parsed.period, request_text)
    severity_order, severity_rules = _fill_severity_defaults(
        checks, parsed.severity_order, [r.model_dump(exclude_none=True) for r in parsed.severity_rules]
    )

    return {
        "request_id": request_id, "source": source, "lender_id": lender_id,
        "period": period, "period_source": period_source, "sample_file": sample_file,
        "checks": checks, "unsupported": unsupported,
        "severity_order": severity_order, "severity_rules": severity_rules,
        "agent_trace": trace,
    }
