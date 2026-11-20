# Build status -- CO-04 Audit evidence pack agent

Team: agent.isAwake() -- CreditNirvana Agentathon, Nov 19-20 2026.

This file is the "what's actually done" record. `README.md` has the run instructions.

## TL;DR

All 10 build steps are done. The pipeline runs end to end against **Postgres**
(not CSV), behind a **FastAPI** backend and a **React** frontend (not
Streamlit -- that app still exists but is superseded). LLM calls go through
**Sarvam's gateway** (Deepseek for reasoning, Sarvam's own model for
conversational transcript review, Sarvam Prism for decision scoring), not
Anthropic. Beyond the 3 canned demo requests, you can now run the agent
against an **uploaded PDF or pasted email text**, or against **real emails
pulled from a connected Gmail inbox** (OAuth, read-only). Since Step 14,
request decomposition is **agentic by default** (`agent/decompose_agent.py`
-- it can investigate guidelines/policy/accounts via tools before
committing to checks); the old always-one-shot `decompose()` is now the
explicit opt-out, not the other way around.

Measured on REQ-01/02/03 against the synthetic ground truth:

| metric | result | target |
|---|---|---|
| gap recall | 100.0% | >= 95% |
| false gap rate | 1.7% | < 5% |
| unsupported claims | 0 | 0 |
| severity accuracy | 100.0% | -- |
| status accuracy | 99.9% | -- |

## What changed from the original plan, and why

The repo started as a CSV + pandas + DuckDB engine with a Streamlit demo in
mind. Two explicit pivots since:

1. **LLM provider**: swapped from Anthropic to Sarvam's gateway, per
   instruction from Ambuj Pratap Jain -- Deepseek (`deepseekv4.1-flash-1017`)
   for generic reasoning (request decomposition, letter drafting), Sarvam
   (`sarvam-105b-conversations`) for conversational work (transcript
   labelling), Sarvam Prism (`sarvam-prism`, `/v2/systemone`) wired up for
   structured decision scoring.
2. **Infra + UI**: "we need docker postgre that is must and ui need to
   improve and everything will move to prod dont think of just demo" ->
   Postgres in Docker replaced the CSV/DuckDB data layer, and React + FastAPI
   replaced the Streamlit demo as the primary UI.

## Step-by-step

- [x] **Steps 1-4** -- data layer, D12 rules engine, the 8 deterministic
  checks, `Verdict`/`severity` grading, `eval/score.py`. Unchanged by any of
  the above -- this is the part that was already proven and is never
  rewritten, only re-pointed at a new data source.
- [x] **Step 5** -- `agent/decompose.py`: Deepseek (via Sarvam) turns request
  text/PDF into a `checks.json`. Closed catalogue (`CHECK_TYPES` from
  `checks.py`) -- anything it can't map goes to `unsupported`, reported, never
  dropped. Verbatim-quote check on every extracted clause, whitespace-normalized
  so line-wrap isn't mistaken for paraphrasing.
- [x] **Step 6** -- `agent/transcript_review.py`: Sarvam's conversational
  model labels each call transcript (`ok` / `disclosure_before_verification` /
  `disclosure_after_failed_verification` / `third_party_disclosure`), one call
  ever per `call_id` (Option B caching), now cached in `audit.transcript_labels`
  (Postgres) instead of a JSON file. Includes a hand-fixed counter-example for
  a systematic "wrong number -> false disclosure" hallucination found during
  testing (false-gap rate dropped from 24-57% to ~1-3% after the fix).
- [x] **Step 7** -- `pack.py` (gap_report.xlsx + request_interpretation.md),
  `letter.py` (drafts a response letter from a summary only, never raw account
  rows), `validator.py` (softening language, unmasked phone numbers, DRAFT
  disclaimer, gap/unsupported mention checks). `draft_and_validate()` retries
  once on validator failure, then ships flagged `needs_human_review` rather
  than silently failing.
- [x] **Step 8** -- original Streamlit demo (`app.py`). Still in the repo and
  runnable, but **superseded** by Step 9 as the primary UI.
- [x] **Step 9** -- production path:
  - `docker-compose.yml` + `sql/001_schema.sql`: Postgres 16, one schema
    (`audit`), 10 source-mirror tables (all-TEXT columns, no PK on the natural
    ID -- the data has genuine seeded duplicates the Python layer is built to
    detect) + 4 generated-data tables with real PKs (`transcript_labels`,
    `runs`, `verdicts`, `gap_items`).
  - `scripts/load_to_postgres.py`: idempotent CSV -> Postgres loader
    (`TRUNCATE` + `COPY FROM STDIN`).
  - `agent/tools.py`: `_raw()` is the *only* method that changed -- CSV read
    became a Postgres `SELECT *`. Every method built on top of it is
    byte-identical in behavior; proven by re-running REQ-01/02/03 and getting
    the exact same per-check EVIDENCED/GAP/NOT_APPLICABLE counts as the CSV
    era.
  - `agent/run_checks.py`: additively persists every run to
    `audit.runs`/`verdicts`/`gap_items` (wrapped in try/except -- a DB hiccup
    never breaks the CSV output, which stays the source of truth for
    `pack.py`/`letter.py`/`eval/score.py`).
  - `api/main.py` (FastAPI): wraps decompose -> run_checks -> pack -> letter
    as background jobs keyed by `request_id`. Endpoints: request catalogue,
    raw text, start run, poll status/log, full result (checks + summary +
    score), filtered gaps, file downloads.
  - `frontend/` (React + Vite): same 5-pane flow as the Streamlit app --
    request picker, live run log, checks/unsupported, run summary, filterable
    gap table with a click-to-inspect evidence drawer, scorecard + letter +
    downloads. Dark/light theme aware, verified mobile-responsive.
- [x] **Step 10** -- three ways in, one pipeline:
  - **Upload your own**: `POST /api/custom/run` accepts a PDF or pasted text
    plus which lender's sample to test against (the dataset only has real
    account data for 3 lenders, independent of what the uploaded letter
    says). Generates its own `CUSTOM-xxxxxxxx` request_id so it never
    collides with the sample requests.
  - **Gmail inbox**: `agent/gmail_client.py` + `/api/inbox/*` -- OAuth
    (`gmail.readonly` + `gmail.send`), lists candidate audit-request emails
    by subject match, a human picks one from the UI and runs the agent on it
    (`GMAIL-<message_id>`). The refresh token lives in `audit.oauth_tokens`,
    one row, one mailbox. Needs a Google Cloud OAuth client the operator
    creates themselves -- see README.md "Gmail inbox setup". Fully coded;
    not live-tested end-to-end in this session since that requires a real
    Google OAuth client and consent grant (can't be done without the
    operator's own Google login).
  - **Sending the reply back**: `POST /api/requests/{id}/send-reply`
    (`gmail_client.send_reply`), reachable ONLY from a Gmail-sourced run and
    ONLY via an explicit button click in the new `ReplyPane` -- nothing in
    this codebase ever calls it automatically. The recipient, subject, and
    thread/`In-Reply-To` headers are re-fetched from the real Gmail message
    at send time (`get_message_meta`), never trusted from the browser. The
    UI shows the recipient, lets the human edit the letter text before
    sending, requires an extra "I've reviewed this" checkbox when the
    validator flagged `needs_human_review`, and a native browser confirm on
    top of that. A reply can only be sent once per request_id
    (`_jobs[id]["replied"]` guards against a double-send). Deliberately
    manual-only -- see the design discussion in this session's transcript:
    auto-sending an unreviewed compliance letter to a real lender is a real
    legal/reputational risk, not a UX nicety to skip.
  - UI: added a 3-tab picker (Sample requests / Upload your own / Gmail
    inbox) and a visual pass (tab bar, card shadows, consistent
    field/button styling, pill badges) across the React frontend.
  - **Bug found and fixed while testing this**: `decompose.py` could return
    an empty `severity_order`/`severity_rules` when the source request had
    no severity/grading section of its own (realistic for a short pasted
    email) -- `severity.grade()` then raised "no rule matches" and the run
    crashed. Fixed with a code-level backstop
    (`_fill_severity_defaults` in `agent/decompose.py`): every check_type
    actually extracted is guaranteed at least one severity rule, defaulting
    sensibly (consent/verification/certification -> CRITICAL, template/
    hours/recording/notice -> MAJOR, frequency -> MINOR) when the model
    didn't write one itself. A check_type the model DID grade (even
    partially, with `gap_types`) is left untouched.

## Verified live (this session)

- `pytest -q` -- 7 passed, no regression from the CSV -> Postgres swap.
- REQ-01/02/03 run against Postgres with results byte-identical to the
  earlier CSV-proven run.
- Postgres persistence independently queried: `audit.runs` (3 rows),
  `audit.verdicts` (grouped by status -- matches each run's printed summary),
  `audit.gap_items` (one row per finding).
- FastAPI: all 7 endpoints exercised via curl against a real run (REQ-01) --
  202 on start, log progression on poll, correct `result`/`gaps`/3 downloads.
- React: triggered a real run from the browser, watched the log stream live,
  confirmed every pane rendered correctly, clicked a gap row and confirmed the
  evidence drawer showed the exact record, confirmed scorecard metrics and
  download links, confirmed no console errors, found and fixed one real
  mobile-layout bug (summary table caused horizontal overflow at 375px).
- Step 10 upload flow: tested via curl (PDF upload, pasted text, missing
  file+text -> 400, unknown lender_id -> 400) and live in the browser (both
  the PDF and paste-text paths, including the full UI transition from
  "Running..." to rendered Checks/Summary/Gaps/Scorecard panes without a page
  reload, driven entirely by the frontend's own status polling). The
  severity-defaults bug above was caught by this testing, not invented --
  the minimal one-sentence pasted request is a realistic case, not an edge
  case contrived to find a bug.
- Step 10 Gmail flow: `/api/inbox/status` confirmed to correctly report
  `{"configured": false}` with no Google credentials set, and the UI
  correctly shows the "not configured" message on the Gmail inbox tab. The
  OAuth exchange / message listing / message-run code paths are written
  against the real Gmail API client library but could not be exercised
  end-to-end without a real Google OAuth client and a human consent grant.

## Step 11 -- Gmail inbox: persistent status, not just a live search

Previously, "Refresh inbox" re-ran the Gmail search fresh every time -- an
email already run, or already replied to, looked identical to one never
touched. `agent/inbox_runs.py` + `audit.inbox_runs` (`sql/005_inbox_runs.sql`)
fix that with one lifecycle per message:

```
NEW -> PROCESSING -> NEEDS_REVIEW -> REPLIED
                  \-> ERROR
```

- `GET /api/inbox/messages` now inserts a `NEW` row (with a Prism confidence
  score) for anything not seen before, and returns the persisted status for
  anything already tracked -- `sync_new_messages()` never re-classifies or
  duplicates a row on a later refresh (verified against the real connected
  mailbox: `created_at == updated_at` held after a second refresh).
- The Prism call (`sarvam-prism` / `/v2/systemone`) had never actually been
  exercised before this -- its request shape was discovered by iterating on
  real 422 errors from the live API, not guessed: a question needs
  `{"type": "noul", "instructions": "..."}`, returning a 0-1 probability
  under `answers.<key>.noul`. Confidence is purely informational -- it's
  shown as a caption in the UI, never filters or hides a message. Verified
  live: a real audit-request email scored 71.8%, an unrelated email scored
  3.3%, and on the actual connected mailbox, a GitHub notification whose repo
  name happens to contain "audit" (matching the coarse keyword search) was
  correctly scored 3.3% likely to be real.
- `start_inbox_run` flips to `PROCESSING`; `_execute()`'s existing
  success/error branches flip to `NEEDS_REVIEW`/`ERROR` (one `if
  request_id.startswith("GMAIL-")` each, no new parameters threaded through
  the generic pipeline function); `/send-reply` flips to `REPLIED`.
- UI: each inbox row now shows a status chip and the confidence caption;
  clicking a `NEEDS_REVIEW`/`ERROR`/`REPLIED` row jumps straight to that run's
  result instead of needing to remember which account list/dates were used.
- **Revised after the first live test**: the user explicitly asked for two
  changes to the original manual-only design, after seeing it in practice --
  (1) hide emails Prism scores as not a real audit request, rather than just
  captioning them, and (2) auto-start a run with no click at all when a `NEW`
  email is classified `is_audit_request=True`. Both are now built, with one
  guardrail kept: auto-start only fires when the email body itself names real
  account ids (`\bACC\d+\b`) -- there's no human in that path to attach an
  account list, so a qualifying email with no inline ids still waits for a
  manual click. Verified against the real connected mailbox: a genuine audit
  request (confidence 85%) auto-started on refresh with zero clicks and
  produced a correct result (4/4 accounts flagged for a real seeded
  WhatsApp-consent gap, CRITICAL); an unrelated GitHub notification
  (confidence 3%) no longer appears in the list at all.
- Also fixed in the same pass: the "Run agent on this email" button
  previously *required* an attached account-list file even though the
  backend already extracts account ids straight from the email body --
  real frontend/backend mismatch, not user error. `canRun` no longer
  requires a file.

## Parallel work landed from likhithg-png/audit-agent (synced into this branch)

likhith's team (with their own Claude Code session) independently built
almost exactly the RBI-guideline-driven check expansion discussed in this
session: a guideline catalogue (`audit.guidelines`, 16 entries, each labeled
`RBI_VERIFIED`/`LENDER_POLICY`/`UNVERIFIED` with its source) and 6 new check
types -- `CALLER_NUMBER_SERIES`, `MESSAGE_CONTENT`, `CALL_CONDUCT`,
`FIELD_VISIT`, `SECURITY_RELEASE`, `PENAL_CHARGES` -- with real synthetic
data and ground truth. Notably, the harassment/abusive-language check
(`CALL_CONDUCT`) was built as deterministic regex + a QA-logged conduct
table rather than an LLM call, more consistent with this project's actual
"deterministic code decides verdicts" design than the LLM-based version
proposed earlier in this session. Rebased cleanly; all data loaded into
Postgres; verified one new check live end-to-end (real seeded harassment
language caught with correct citations). Not yet done: these 6 checks aren't
wired into `eval/score.py`'s recall/false-gap-rate scoring yet, and the
`DND_COMPLIANCE`/agent-background-verification checks discussed earlier in
this session still aren't built.

## Step 12 -- attachments, and making a finished run survive a restart

Three things came out of the first real round-trip through a live mailbox:

- **Attachments.** The reply carried only prose; the evidence pack was
  download-only in the UI. `send_reply` now takes an `attachments` list and
  nests the `multipart/alternative` body inside a `multipart/mixed`, and
  `ReplyPane` offers the same three files the Downloads row does
  (gap_report.xlsx / request_interpretation.md / response_letter_DRAFT.docx).
  Nothing is attached unless a human ticks it, and the confirm dialog says how
  many are going.
- **A finished run no longer dies with the process** (`sql/006_run_results.sql`
  + `agent/run_results.py`). The drafted letter and the reply metadata were the
  only things living purely in `_jobs`; everything else `/result` returns was
  already on disk. That meant a Gmail message sitting at `NEEDS_REVIEW` became
  permanently unreachable the moment uvicorn reloaded -- the status row said "a
  human still needs to review this" while the draft it pointed at was gone, so
  the reply could never be sent. In production that would strand pending work
  on every deploy. Both are now persisted, `/result` falls back to disk + DB,
  and `/send-reply` reads the recipient from the DB when memory is empty.
  Verified by running a real Gmail message, restarting the API, and confirming
  the draft, recipient and all result panes still render.
- **Two bugs found in the previous commit's own code**, both via the live
  test: (1) the "hide non-audit emails" filter hid messages that had *already
  been processed*, stranding a `NEEDS_REVIEW` run where no human could reach
  it -- the filter now only applies while a message is still `NEW`; (2)
  `onViewExisting` only loaded a result when the in-memory status said
  `"done"`, so after a restart ("idle") it silently rendered nothing.

## Step 13 -- a genuinely tool-using decompose (experimental, opt-in)

Previously the request -> `checks.json` step was always one-shot: the model saw
only the request text and had to guess at severity, lender policy, and
whether named account ids were real. `agent/decompose_agent.py` gives it
tools instead (`list_guidelines`, `lookup_policy`, `query_accounts`,
`run_sql` read-only against the `audit` schema, `ask_clarifying_question` to
bail out instead of guessing), wired through `call_with_tools()`
(`agent/llm.py`) -- a native OpenAI-shape `tool_calls` loop against
Deepseek/Sarvam, capped at `MAX_STEPS = 6` rounds.

- **Additive, not a replacement**: `decompose()` is untouched and stays the
  default everywhere. The agentic path only runs when a caller opts in --
  `use_agent` on `POST /api/custom/run`, surfaced as an "Use agentic
  decompose (experimental)" checkbox in the Upload pane. The output goes
  through the exact same validation as the one-shot path (closed
  `CHECK_TYPES` catalogue, verbatim-quote check, severity-defaults backstop)
  -- nothing the agent "decides" bypasses what already catches bad output.
- **The investigation trail is the point**: every tool call made is kept as
  `agent_trace` in `checks.json`, returned from `/result`, and rendered in
  `ChecksPane.jsx` as an expandable "Agent investigated before deciding (N
  lookups)" callout -- a human can see exactly what the model looked at
  before committing to checks, not just the final answer.
- **Verified live (this session)**: ran a real pasted request ("For accounts
  ACC100001, ACC100003 and ACC100007: ... collection calls respected the
  permitted calling hours and call frequency limits ... WhatsApp reminders
  were sent only to borrowers who had consented") through the real UI with
  the checkbox on. The agent made 6 real tool calls against live
  Sarvam/Deepseek and live Postgres -- `query_accounts`, `list_guidelines`,
  `lookup_policy`, and two exploratory `run_sql` schema lookups -- then
  produced 3 valid checks / 0 unsupported, ran end to end through
  pack/letter (validator passed, DRAFT disclaimer present), and rendered
  correctly in every pane. `agent_trace` confirmed persisted in
  `out/<request_id>/checks.json` across a second run. `pytest -q` still 7
  passed -- no regression from adding this path.
- First real run surfaced a genuine UX gap, not a bug: a request with no
  explicit period and no attached account list fails with a clear inline
  error ("Could not find an explicit audit period...") rather than guessing
  -- correct behavior, just confirms the UI's period fields are load-bearing
  for short/vague pasted requests, same as the one-shot path.
- **Now measured** (`eval/eval_decompose_agent.py`, new): ran
  `decompose_with_tools()` against the three real REQ-01/02/03 request files
  (not hand_checks -- the actual `.txt`/`.pdf` text `decompose()` would see in
  production), fed its output through the same deterministic engine and the
  same `eval/score.py` metrics, into a separate `out_agent_eval/` dir so the
  proven `out/REQ-*/` baseline is untouched. Result: **identical to the
  one-shot baseline on every metric** -- 100.0% gap recall, 1.7% false gap
  rate, 0 unsupported claims, 100.0% severity accuracy, 99.9% status accuracy.
  REQ-01 reproduced the exact same 5 checks (`C1`-`C5`, same check_types, same
  clauses) as the hand-authored ground truth with *zero* tool calls -- the
  email already stated everything, so it correctly chose not to investigate.
  REQ-02/REQ-03 each made 2 tool calls (`lookup_policy`, `list_guidelines`)
  before landing on checks that still matched ground truth exactly. The 3
  status mismatches that remain are the *same* pre-existing ones already in
  the one-shot baseline (same account_ids, same check_ids, same
  `VERIFICATION_BEFORE_DISCLOSURE` transcript-labeling edge case) -- confirmed
  by re-running `eval/score.py` and diffing the mismatch tables, not assumed.
  Not a large-n result (3 requests, 178 true gaps) and doesn't cover a
  genuinely ambiguous-severity case since none of REQ-01/02/03 are that --
  still open as the next thing worth testing if the agentic path is ever
  promoted off "experimental."

### Ambiguous-severity case -- now covered, both as a fast unit test and live

The gap just above (no REQ has a request that states zero severity info) is
now closed two ways:

- **`tests/test_decompose.py`** (new, runs in every `pytest -q`, no LLM call):
  unit tests `_fill_severity_defaults` directly on synthetic input shaped like
  what a real ambiguous request decomposes to -- zero severity info at all,
  a partially-graded scale, and a fully-graded one as a sanity check that the
  backstop doesn't interfere when it isn't needed. Each test also calls
  `severity.grade()` on the result, not just inspects the rules list shape --
  the actual regression being guarded against is "no rule matches," a
  `ValueError`, not a malformed dict.
- **`eval/eval_decompose_agent.py::eval_ambiguous_severity_case()`** (new,
  live, not part of `pytest -q`): runs `decompose_with_tools()` against a real
  LLM call on a request with NO severity section at all ("Please confirm that
  collection calls respected the permitted calling hours and call frequency
  limits, and that WhatsApp reminders were sent only to borrowers who had
  consented..."), against L001's real account data. No ground truth exists
  for this ad hoc text so it can't score gap recall -- what it proves is
  narrower but real: the live, non-deterministic agent (1 tool call this run,
  vs. 0 and 2 on other phrasings of similar text in earlier runs -- tool-use
  decisions aren't stable across runs) still produces a `severity_order` of
  `['CRITICAL']` and a default rule for every extracted check_type, and
  `severity.grade()` succeeds for all three with the real gap_type each
  check_type actually produces (`outside_calling_hours`,
  `daily_cap_exceeded`, `no_consent_record`) -- not a placeholder string.
- **Correction to this file**: Step 10's original bug-fix note (above) claimed
  the backstop defaults "sensibly (consent/verification/certification ->
  CRITICAL, template/hours/recording/notice -> MAJOR, frequency -> MINOR)."
  That per-check-type mapping is only the *prompt's* worked example teaching
  the model a pattern -- reading `_fill_severity_defaults` itself
  (`agent/decompose.py`) while writing this test showed the actual code-level
  fallback is simpler and blunter: every check_type with zero rules gets
  `severity_order[0]` (the single worst level of whatever scale is in play,
  or `CRITICAL` if there's no scale at all) -- there is no per-check-type
  table in code. The tests above assert the real behavior, not the
  previously-written description of it.

### `run_sql` hardening -- a real read-only Postgres role, not just a string check

The `run_sql` tool above lets the model write its own SQL (it did, live, twice --
both schema-introspection queries it composed itself). The only guard on that was
a string check in `agent/tools.py` (reject anything not starting `select`/`with`,
or containing an insert/update/delete/drop/create/alter/copy keyword) -- a text
filter, not a real permission boundary.

- `sql/008_readonly_role.sql` creates `audit_readonly`, a real Postgres LOGIN role
  with `CONNECT` + schema `USAGE` + `SELECT` on every table in `audit` (plus
  `ALTER DEFAULT PRIVILEGES` so it covers tables from later migrations too) --
  nothing else. No INSERT/UPDATE/DELETE/DDL grant exists for it to have.
- `agent/db.py` gained `connect_readonly()`; `agent/tools.py`'s `Tools.sql()` --
  the one function LLM-written query text ever reaches -- now connects as
  `audit_readonly` instead of the superuser `audit` role. The string check stays
  as a cheap first-line filter (clean error message instead of a raw Postgres
  one), but the database role is the actual enforcement now.
- **Verified**: applied the migration to the running container
  (`docker exec ... psql < sql/008_readonly_role.sql`, since
  `docker-entrypoint-initdb.d` only runs on first boot of a fresh volume, and
  this one wasn't). Confirmed `audit_readonly` can `SELECT` (2000 rows from
  `audit.accounts`) and that a `DELETE` -- even wrapped in a transaction that
  would've rolled back regardless -- is rejected at the permission layer before
  touching a row (`permission denied for table accounts`). Re-ran the full
  agentic flow end to end through the live API afterward (`query_accounts`,
  `list_guidelines`, `lookup_policy`, `run_sql` all firing, run reaching
  `Done`) to confirm the role swap didn't break the tool. `pytest -q` still 7
  passed.
- Caveat: the role's password is a fixed value committed in
  `sql/008_readonly_role.sql` and `.env.example` -- fine for this hackathon's
  local-only Postgres (port 55433, not exposed), not something to carry as-is
  into a real deployment.

## Step 14 -- decompose_agent.py becomes the default, not experimental

With the eval above showing no regression on REQ-01/02/03 and the ambiguous-
severity backstop now covered by a test, the tool-using decompose is now
what every entry point uses unless something explicitly opts out:

- `api/main.py`'s `_execute()` default flipped from `use_agent: bool = False`
  to `True`. The sample picker (`/api/requests/{id}/run`) and the Gmail
  paths never passed `use_agent` explicitly, so they inherit this -- no
  other code change was needed for them to start using the agentic path.
  `/api/custom/run`'s Form default flipped the same way.
- The UploadPane checkbox inverted to match: it was "Use agentic decompose
  (experimental)", default unchecked. It's now "Skip investigation (faster,
  one-shot)", still default unchecked -- so doing nothing now gets the
  agentic path, and the box is how you opt back OUT to the old always-on
  one-shot behavior, same as before Step 14.
- `decompose.py` is NOT retired -- it's still the explicit-opt-out path, and
  `decompose_agent.py` still imports its validation/schema/prompt pieces
  (`CHECK_TYPES`, `Decomposition`, `SYSTEM_PROMPT`, `_fill_severity_defaults`,
  etc.), so it couldn't be removed even if that were the goal.
- **Verified live, through the real entry points, not just the eval script**:
  triggered REQ-01/02/03 via the actual `/api/requests/{id}/run` endpoint
  (the sample picker, zero params about use_agent) and confirmed each run's
  log shows "Using the tool-using agent..." -- the default genuinely changed
  for the real app, not just in a side script. Re-ran `eval/score.py`
  (reading the regenerated `out/REQ-*/`) and got the identical numbers as
  every prior measurement: 100.0% gap recall, 1.7% false gap rate, 0
  unsupported claims, 100.0% severity accuracy, 99.9% status accuracy, same
  3 pre-existing status mismatches. Also confirmed the explicit opt-out
  (`use_agent=false` via `/api/custom/run`) still produces a log with no
  "tool-using agent" line, i.e. the escape hatch works. Confirmed in the
  browser that the UploadPane checkbox now renders the new "Skip
  investigation (faster, one-shot)" label, unchecked by default.
- `tests/`, `eval/eval_decompose_agent.py` and `eval/hand_checks/*.json`
  (the CLI-only `python -m agent.run_checks --all` path) are untouched --
  none of them call `decompose()`/`decompose_agent()` at all, so this change
  only affects the three live API entry points.

## Step 15 -- Gmail inbox was silently missing real requests with no trigger keyword

Found live, not hypothesized: a genuine audit request arrived in the
connected mailbox from a real teammate (pannaga.n@creditnirvana.ai) and
never appeared in the Gmail inbox tab no matter how many times "Refresh
inbox" was clicked.

- **Root cause**: the email's `Subject` header was literally empty, and the
  body text never contains "audit", "compliance", or "annexure" either (it
  only says "Please proide evidce for acconts ACC100011..." -- a typo'd,
  informal version of the same text Likhith's test emails use). The old
  `GMAIL_SEARCH_QUERY` (`subject:(audit OR compliance OR annexure OR "audit
  request") newer_than:180d`) only ever searches the subject line, so a
  blank-subject request was invisible by construction -- no amount of
  re-syncing would have found it, and broadening to full-text search
  wouldn't have helped either, since the trigger words aren't in the body.
- **Fix**: `agent/config.py`'s `GMAIL_SEARCH_QUERY` default widened to just
  `newer_than:180d` -- no keyword gate at all. Prism (`agent/inbox_runs.py`
  `_classify()`) is now the only filter deciding `is_audit_request`, which
  it was already proven to do correctly (Step 11: 71.8-85% on real requests,
  3.3% on a GitHub notification). Tradeoff accepted deliberately: one more
  Prism call per irrelevant email that lands in the inbox, in exchange for
  never silently missing a real request over a wording choice.
- **Verified live end to end, on the actual email that exposed the bug, not
  a synthetic repro**: after the fix, `/api/inbox/messages?sync=true`
  returned Pannaga's message with `is_audit_request: true` (confidence
  67.9%) despite the typos and blank subject. Because the body also named
  real account ids, Step 11's existing auto-start fired with zero clicks
  (`status: PROCESSING` the moment it synced) -- confirmed the run reached
  `done`: `query_accounts` tool call on the 4 named accounts, 5 checks
  extracted, period read correctly from the request text, letter drafted.
  No new bug in the auto-start/decompose path -- it's exactly Step 11/14's
  existing behavior, just finally able to see this email at all.
- `README.md` updated to describe the new default and why (Gmail inbox
  setup section + the env var table).
- Caveat carried over, now more relevant: `list_candidate_messages`'s
  `max_results=30` per sync is unchanged. A wider search returns more
  candidates per call, so a mailbox with a genuinely high volume of
  unrelated mail between refreshes could now push a real request past that
  cap before it's ever seen. Not hit in practice here, not fixed either.

## Step 16 -- the Accuracy scorecard was showing the wrong numbers, on every run

Found while explaining the scorecard to the user: `ScorecardPane.jsx`'s own
comment says it only renders "when ground truth exists for this request,"
but `api/main.py`'s old `/result` handler gated it on
`ground_truth_account_check.csv` *existing on disk* (always true) and then
called `eval.score.score()`'s `overall` -- a metric blended across every
`out/REQ-*/` currently on disk, completely unscoped to the request actually
being viewed. That's why a custom upload or a Gmail run (e.g. Pannaga's,
Step 15) showed the exact same "100.0% / 1.7% / 0" every time: it was
always just whatever REQ-01/02/03 currently score, shown regardless of what
you were looking at -- never that request's own accuracy, which doesn't
exist for a real one-off email/upload in the first place.

- **Fix**: `/result` now gates the whole scorecard on `request_id in
  SAMPLE_REQUESTS` (i.e. actually REQ-01/02/03), and rebuilds `recall` /
  `false_gap_rate` / `unsupported_claims` from `per_check`'s per-check counts
  (`true_gaps`/`found`/`false_gaps`/`unsupported_claims`) summed for just
  that `request_id`, instead of reusing the global `overall`. The `per_check`
  breakdown itself was already correctly request-scoped -- it just wasn't
  being used to compute the three headline numbers.
- **Verified**: REQ-01's `/result` now returns `recall: 1.0, false_gap_rate:
  0.0` (its own 5 checks only); REQ-02 now returns `false_gap_rate:
  1.64%` -- genuinely its own number, not the 1.7% blended figure every
  request used to show. Pannaga's Gmail run (`GMAIL-1a1f6cbec647775e`) now
  returns `score: null`, so `ScorecardPane` correctly renders nothing for it.
  `pytest -q` still 10/10.
- Also removed the account-list file field and period-from/to fields
  entirely from the Gmail inbox's per-message run panel (`InboxPane.jsx`'s
  `MessageRow` no longer imports `AccountsField`/`PeriodFields` at all, and
  no longer carries `accountsFile`/`periodFrom`/`periodTo` state) -- a
  Gmail-sourced run always has the real email to read both from (the same
  fallback the auto-start path already uses), so the override UI was dead
  weight there. `submit()` now just posts an empty form to
  `/api/inbox/messages/{id}/run`, which already defaults every param to
  `None`/falls back to extracting from the email text -- confirmed nothing
  on the backend needed to change. `UploadPane.jsx` is untouched (its
  account-list/period fields and hint text stay -- a pasted/uploaded
  request has no email to fall back to, so the override there is load-
  bearing, not redundant). An earlier, narrower version of this fix only
  hid the period hint TEXT via a `showHint` prop on `PeriodFields`; once the
  user clarified the whole fields should go, that prop was reverted since
  nothing calls it with `false` anymore.
- **Verified live in the browser, including the "Run again" button with
  zero fields to fill in**: expanded Pannaga's message row -- confirmed only
  "View result" / "Run again" render, no account-list or period UI at all.
  Clicked "Run again" with nothing supplied: log showed
  `query_accounts` on the 4 ids named in the email body, 5 checks extracted,
  period read from the request text, run reached `Done` with 4 real CRITICAL
  WhatsApp-consent gaps found, letter drafted, validator passed. Also
  visually confirmed the Step 16 scorecard fix on this same run: no
  "Accuracy scorecard" section rendered at all for this `GMAIL-*` request,
  as intended.

## Step 17 -- UI rebuilt: two-column workspace, and the row-overflow bug fixed properly

The user's complaint was that the UI felt static, the inbox's "from / date /
status" line didn't fit, and the Upload tab was "unsorted". All three were
real and all three had structural causes, not cosmetic ones:

- **Layout was a single flat column** of 6+ full-width cards, so the form
  scrolled away the moment results rendered. Now a **two-column workspace**
  (`.workspace`, `408px` source column + fluid results column): the request
  form / inbox stays put (`position: sticky`) on the left while results
  scroll on the right, collapsing to one column under 1120px. Results get a
  sticky jump-rail (Result / Checks / Summary / Gaps / Letter) and a proper
  empty state instead of a blank page before the first run.
- **The inbox row overflow was a CSS bug, not just long text.** The row was a
  flexbox whose text child had the default `min-width: auto`, so a long
  `"Pannaga N (Work)" <pannaga.n@creditnirvana.ai> -- Fri, 20 Nov 2026
  08:01:49 +0530` line could not shrink and blew the row out. Rebuilt as
  `grid-template-columns: minmax(0, 1fr) auto` with `min-width: 0` on the
  text cell, plus: the raw `From` header is parsed into name + address
  (address moves to a `title` tooltip), the date is formatted
  ("Nov 20, 08:01 AM" instead of the full RFC string), the snippet is clamped
  to 2 lines, a blank subject renders as a muted "(no subject)" instead of
  nothing, and the "68% likely an audit request" text line became a compact
  meter chip. Measured after: **0 overflowing rows** in a 408px column.
- **Upload tab "unsorted"**: it stacked a file input, an "or" divider, a
  textarea, an accounts field, a 16-item guideline wall (expanded by
  default) and the period fields in one run-on scroll. Now a segmented
  **Paste text / Upload file** switch (mutually exclusive, so only one input
  is ever shown and switching clears the other), with accounts+period,
  guidelines, and the one-shot toggle each behind a collapsed `<details>`
  that reports its own state in the summary ("— 2 selected", "— optional").
  The whole form is now 563px tall and fits on screen without scrolling;
  before, it was a full-page scroll.
- Also refreshed the design tokens (deeper dark palette, light-mode pair,
  spacing/radius scale, custom scrollbars), added a card entry animation
  gated behind `prefers-reduced-motion`, and smooth anchor scrolling.
- **Found and fixed a genuine responsive bug while verifying**, not
  hypothesized: at 390px the page scrolled horizontally by 27px. Two causes,
  both traced by walking the DOM: (1) `.pane` is a flex item of
  `.col-results` and inherited `min-width: auto`, so a wide table pushed the
  pane past the viewport instead of scrolling inside its own
  `.table-scroll`; (2) the agent-trace `<pre class="evidence">` was
  **1883px** wide because `<pre>` preserves whitespace and never wraps.
  Fixed with `min-width: 0` on the flex children and `white-space: pre-wrap`
  + `overflow: auto` on `pre.evidence`. Re-measured: 0 overflow at 390px and
  at 1440px.
- **Verified**: measured layout geometry at 1440px and 390px (grid columns,
  per-pane and per-row overflow, table containment), light and dark schemes
  both flipping correctly, and ran a real end-to-end request through the
  rebuilt form (empty state -> live log -> rail + tiles + all result panes,
  scorecard correctly absent on a `CUSTOM-*` run per Step 16).
  `npx vite build` clean, `pytest -q` 10/10. Caveat: the Browser pane's
  screenshot action timed out on every attempt this session and the
  Claude-in-Chrome extension disconnected partway, so this was verified by
  DOM measurement and a production build rather than by eye -- the geometry
  claims above are measured, but no one has actually *looked* at the result.

## Step 18 -- account ids in an attachment (and why the auto-start gate stays)

Question that exposed it: "can we gate auto-start on the Prism score alone --
and what if the account ids are in an attachment?" Both halves turned out to
matter, and one was a regression from Step 16.

- **Accounts in an attachment did not work at all on the Gmail path.** Three
  compounding reasons: `gmail_client.get_message_text()` only ever extracted a
  **PDF** attachment (CSV/XLSX were ignored entirely); `_try_autostart`'s
  `\bACC\d+\b` regex therefore never saw those ids; and `_begin_gmail_run`
  never passed any attachment bytes to `_resolve_sample`. Confirmed
  empirically, not assumed -- calling `_resolve_sample` with no annexure and
  no inline ids raises `no accounts to test`. **Step 16 made this worse**:
  removing the account-list field from the Gmail pane removed the only manual
  escape hatch, so such an email became entirely untestable from the inbox.
  That was a real regression introduced in this session.
- **Fix**: `gmail_client.find_account_list()` (new) pulls the first
  `.csv`/`.xlsx`/`.xls` attachment -- deliberately separate from
  `get_message_text()`, which reads the request *prose* and prefers a PDF
  letter. `_begin_gmail_run` now falls back to it when no account list was
  supplied, so an emailed Annexure A is used automatically.
- **Answering the Prism-only question: no, the gate should not be Prism
  alone.** Prism scores whether the *prose* reads like a genuine audit
  request; it says nothing about whether the accounts to test are reachable.
  Auto-starting on the score alone would convert "quietly not started" into a
  run that immediately errors on `no accounts to test` and lands in ERROR.
  The gate is now `Prism said yes AND (ids in the body OR an account-list
  attachment exists)` -- strictly broader than before (it now catches the
  attachment case) without auto-starting runs that cannot succeed.
- **Feedback-loop guard, found while testing against the real mailbox**:
  scanning for spreadsheet attachments matched five messages, all of which
  were **this app's own `gap_report.xlsx` replies** that had landed back in
  the inbox. Without a guard the agent's output would be read as its next
  input. `OWN_ARTIFACTS` now excludes `gap_report.xlsx` /
  `request_interpretation.md` / `response_letter_DRAFT.docx` from the
  account-list search. (It would have failed safely either way -- that
  workbook's first sheet is `Summary`, which has no `account_id` column -- but
  relying on that is luck, not design.)
- **Verified**: synthetic payloads prove the guard (own artifact alone -> None;
  a real `Annexure_A.csv` still found, including nested and when both are
  present; `_find_pdf_attachment` unchanged), and against the live mailbox the
  two messages that previously matched now correctly return `None`.
  `pytest -q` 10/10.
- Not done: the Gmail pane still has no manual account-list upload (Step 16
  removed it). That is now fine for emails that attach their Annexure A or
  name ids inline, but an email that does *neither* still cannot be run from
  the inbox -- it has to go through the Upload tab. Worth restoring a manual
  override there if that case shows up in practice.

## Step 19 -- how raw text is actually handled, and widening the period parser

Question: how does a free-text request like "do audit for ACC100001 from June 1
2026 to 30 June 2026, do for all (consent before contact, template approval,
...)" get handled, and can it be made more forgiving? Tested rather than
reasoned about:

- **Raw text -> checks is already tolerant.** The exact text above produced
  **16 checks, 0 unsupported** -- all 14 named check types mapped, with
  `CONSENT_BEFORE_CONTACT` fanned out to WHATSAPP/SMS/VOICE. The typo "aduit"
  was irrelevant. Two validators keep it honest: the closed `CHECK_TYPES`
  catalogue, and the verbatim-quote rule (a check's `source_text` must appear
  word-for-word in the request, else it lands in `unsupported`).
- **"do all checks" with no list also works** -- tested separately; the model
  quotes the literal phrase `"do all checks"` for all 16 checks, which
  satisfies the verbatim rule. Caveat worth knowing: provenance degrades,
  since every check then cites the same four words instead of a specific
  requirement, making `request_interpretation.md` far weaker as an audit
  trail than when the checks are named explicitly.
- **The period parser was the real brittleness**, and the user had already
  noticed it. `"Q2 2026"` hard-failed with "Could not find an explicit audit
  period". Two separate causes, both needed fixing:
  1. `_dates_in()` only recognised fully-written dates, so a quarter or bare
     month matched nothing; and
  2. the system prompt *explicitly instructed* the model to refuse them
     ("A named quarter or month ("Q1 2026", "Jan-Mar") ... is NOT enough --
     then set period to null"). Fixing only the code left it still failing --
     the model returned `period: None` and never even attempted it. Found by
     dumping the model's actual output rather than assuming the code fix was
     sufficient.
- **Now accepted**: calendar quarters (`Q2 2026`), whole months (`June 2026`),
  and month ranges (`January to March 2026`, `Jan 2026 to Mar 2026`), via a
  new `_spans_in()` alongside the existing `_dates_in()`.
- **Quarters are CALENDAR quarters (Q1 = Jan-Mar), chosen deliberately.** An
  Indian fiscal reading (Q1 = Apr-Jun) is the norm for an NBFC, but it
  contradicts this project's own REQ-01, whose subject says "Q1 2026" while
  its period is 2026-01-01..2026-03-31 -- fiscal semantics would mis-parse the
  flagship sample request. Confirmed against the data before choosing.
- **Still refused, on purpose**: relative wording ("last quarter", "previous
  month") would make the audited window depend on *when the run happened*,
  which is wrong for a reproducible compliance record; and `FY2026` is
  ambiguous in exactly the Apr-Mar vs Jan-Dec way above.
- **Subtle bug caught by its own test**: `"1 June 2026"` contains the substring
  `"June 2026"`. A naive month regex would widen a single named date into the
  whole month, silently auditing 30 days instead of 1. `_MONTH_YEAR_RE` carries
  an optional leading-day group and skips the match when it fires;
  `test_a_single_date_is_never_widened_to_its_whole_month` locks that in.
- **Verified**: 7 new tests in `tests/test_decompose.py` (17 total passing);
  live model output confirmed for `Q2 2026` -> 2026-04-01..2026-06-30,
  `June 2026` -> 2026-06-01..2026-06-30, and `last quarter` -> still refused;
  full end-to-end runs for both the one-shot and agentic paths; and
  `eval/score.py` re-run after the prompt change with **no regression**
  (100.0% recall, 1.7% false-gap, 0 unsupported, 99.9% status accuracy).
- **Reliability finding worth flagging, since Step 14 made the agentic path
  the default**: on the `Q2 2026` request the tool-using agent hard-failed once
  with `hit the 6-step limit without a final answer`, then succeeded on an
  identical retry. Same input, different outcome -- `MAX_STEPS` exhaustion is
  non-deterministic and surfaces to the user as a failed run, where the
  one-shot path handled the same text deterministically. Not fixed here. A
  sensible mitigation would be falling back to `decompose()` instead of
  raising when the step limit is hit.

## Step 20 -- raw internals were leaking into the UI (and three bugs behind them)

The UI was showing developer-facing junk to whoever runs the audit: raw SQL,
Python dict reprs and stray quote characters. Fixing it surfaced three real
defects underneath.

- **The leaks themselves.** The run log built its lines as
  `f"agent called {t['tool']}({t.get('args', {})})"` -- a Python dict repr, so
  the reader saw `agent called run_sql({'query': "SELECT table_name FROM
  information_schema.tables WHERE table_schema='audit'"})`. `ChecksPane` did
  the same with `JSON.stringify(t.args)` in the headline of each trace row.
  Both now go through a shared plain-English describer ("Queried the audit
  data directly", "Checked account ACC100001 exists", "Looked up L001's policy
  rules"). The SQL is **not** hidden -- it moved into the expanded row under a
  "Query it ran" heading, styled as code, because an investigation trail you
  can't inspect defeats the point. Verified live: no `{`, `}` or `SELECT` in
  any summary line, and the queries still render in the expander.
- Also humanised `SummaryPane`'s headers, which came straight off summary.csv
  as engine codes (`check_id`, `CANNOT_DETERMINE`, `NOT_APPLICABLE`), with a
  fallback that lowercases and de-underscores anything unmapped so a new
  status can never render as SHOUTING_SNAKE_CASE. `GapsPane` was left alone --
  it renders structured evidence tables, and those column names are the real
  field names a reviewer needs.
- **Bug 1 -- the agentic path hard-failed, reproducibly.** `decompose_with_tools`
  raised `hit the 6-step limit without a final answer` on
  "do audit for ACC100001 for Q2 2026, do all checks" -- **3 runs out of 3**,
  not flaky. A vague request plus a span period sent it exploring
  `information_schema` instead of committing. Since Step 14 made this the
  default, a reasonable request simply died. It now falls back to the proven
  one-shot `decompose()` instead of raising, and the fallback is recorded in
  the trace and shown in the log ("Investigation ran out of rounds -- used the
  one-shot extractor instead"), never silently.
- **Bug 2 -- the model returns quoted quotes.** Asked to copy words verbatim,
  it returned `source_text` as `"'Q2 2026'"` while the request says `Q2 2026`
  with no quotes, so the verbatim substring check failed **on the quote
  characters alone** and the run died with "Could not find an explicit audit
  period". Confirmed 3/3 live. `_unquote()` now strips matched wrapping quote
  marks (including curly/angle ones) before the check, in both decompose
  paths and for the period -- an apostrophe *inside* a span still survives.
  This was also part of the "stray characters" the user was seeing.
- **Bug 3 -- zero checks crashed with a meaningless error.** When decompose
  returned no checks, `run_checks`' empty pivot surfaced as a bare
  `Error: 'account_id'`. Now caught up front with an actionable message
  telling the user to name the checks or pick them from the Guidelines list.
- **Verified**: `_describe_tool_call` exercised for every tool type; a clean
  end-to-end run whose log reads as prose start to finish; the trace confirmed
  to contain no braces/SQL in summaries while still carrying the queries for
  the expander; 19 tests passing (4 new, covering quote-stripping and a quoted
  quarter resolving); `npx vite build` clean.
- Still open, and worth knowing: the one-shot extractor itself returns **0
  checks** for a bare "do all checks" on roughly 2 attempts in 3 (measured
  directly against the model), so that phrasing is unreliable even with the
  fallback -- it now fails with a clear message instead of a crash, but it
  fails. Naming the checks explicitly, or listing them once in the prompt's
  worked example, would be the real fix.

## Step 21 -- failure handling audited (one serious bug found)

Asked whether failures are handled correctly. Tested each path rather than
read the code and assume. Most were fine; one was not.

**Already correct**: input validation returns clean, actionable 400s with no
stack traces or internals (`no accounts to test -- attach an account list, or
name the account ids...`, `1 accounts do not belong to lender L999`), and
`api.js` surfaces FastAPI's `detail` rather than a bare status code. A run
that crashes mid-pipeline leaves partial files in `out/<id>/` but `/result`
correctly refuses it with 409, because the persisted-letter fallback only
fires when a letter was actually saved. The frontend stops polling on error,
never loads a result, and shows no result panes or Send-reply button.

**Serious bug found -- a failed re-run served the PREVIOUS run's letter.**
`/result` fell through to the persisted copy whenever the in-memory status
wasn't `done`, including when it was `error`. A request_id is a stable slot
(`GMAIL-<message_id>` especially, which the UI exposes as "Run again"), so:
re-run a message, have it fail, and `/result` returned HTTP 200 with the
earlier run's letter **and `replied: true`** -- while `checks.json` on disk
had already been overwritten by the failed run. The UI would render new
checks beside an old letter with Send reply enabled, i.e. a reviewer could
mail a lender a letter that does not describe the run in front of them.
Reproduced directly (`_jobs[rid] = {"status": "error"}` -> full result
returned), then fixed: `error` and `running` now always 409, and only an
`idle` status (the legitimate after-restart case) may fall back to disk.
Re-verified all three states afterwards.

**A clarifying question was being presented as a crash.** When the agent
calls `ask_clarifying_question` -- deliberately choosing to ask rather than
guess, which is the behaviour we want -- the full paragraph was pasted into
both the log line and the raised error, so it rendered twice, in red, under
a "Failed" heading. It is now carried in its own `clarification` field on
`/status`, the log line is short ("Stopped to ask for clarification instead
of guessing"), and `RunLog` renders it once as a warning badge under a
"Needs more information" heading with "Add the missing detail and run it
again". Verified live end to end: heading correct, question shown once, red
error suppressed, no result panes.

**Verified**: 19 tests passing, `npx vite build` clean, and a normal run
re-checked afterwards (`status=done`, `clarification=None`, `/result` 200)
to confirm the error-path changes didn't break the happy path.

**Known gaps, not fixed**: `GET /status` on an unknown request_id returns
200 with an empty idle job instead of 404 (`_job()` uses `setdefault`, so
polling a bogus id silently creates a phantom entry); and a run that fails
after `ask_clarifying_question` on the Gmail path is marked `ERROR` in
`audit.inbox_runs`, which is reasonable but slightly overstates it -- it
needs human input, it didn't crash.

## Step 22 -- forwarded emails were being silently dropped

Question: does a forwarded email work? It did not, and there was already a
real example sitting in the connected mailbox proving it.

- **What actually worked**: `get_message_text()` extracts a forward's body
  fine (1609 chars, original text intact), all four `ACC…` ids were found, and
  the period "1 January 2026 to 31 March 2026" was present. The pipeline would
  have handled it.
- **Where it died**: Prism scored `Fwd: Audit request - Q1 2026 collections
  evidence` at **0.4919** -- a hair under the 0.5 bar -- so
  `is_audit_request=False`, and `list_saved()` hid every `False`+`NEW` row.
  The email was therefore invisible in the UI *and* never auto-started. A
  genuine audit request vanished with no trace for the user. Same failure
  class as Step 15's blank-subject bug, different cause: the forwarding
  chrome ("Fwd:" prefix, the `---------- Forwarded message ----------`
  header block) is pure noise that drags the score down.
- **Fix 1 -- classify the content, not the chrome.** `_strip_forward_markers()`
  removes `Fwd:`/`Re:`/`Fw:`/`Aw:`/`Tr:` prefixes and the quoted header block
  before the Prism call, and the instruction now says "a forwarded request is
  still a request". Measured on the real message: **0.4919 -> 0.7410**.
- **Fix 2 -- separate "confident enough to act" from "confident enough to
  hide".** These were the same 0.5 threshold, which is wrong: auto-starting
  without a human needs real confidence, but *hiding* a message removes the
  human's chance to catch a misclassification and so deserves a much lower
  bar. Now `AUTOSTART_AT = 0.5` and `HIDE_BELOW = 0.25` -- borderline mail is
  listed with its score shown, it just doesn't auto-run. A failed
  classification (`confidence is None`) is shown, never hidden.
- **Verified end to end on the real forwarded message**: it now appears in
  `/api/inbox/messages`, and running it reached `done` -- `query_accounts` on
  all 4 ids, 5 checks extracted, period read correctly from the forwarded
  body, letter drafted. 19 tests still passing.
- **Worth knowing about replies**: `get_message_meta()` reads the `From:`
  header, which on a forward is the **forwarder**, not the original lender.
  Replying therefore goes back to whoever forwarded it. That is the right
  default (answer the person who asked you), but a human should notice it
  before sending -- the recipient is shown on the Send-reply pane, and
  nothing auto-sends.
- **Not fixed**: rows classified before this change keep their old score and
  `is_audit_request` -- `sync_new_messages()` never re-classifies an existing
  row (by design, Step 11). The forwarded message is visible and manually
  runnable now, but will not auto-start until it is re-ingested. Snippet
  stripping is also imperfect for single-line snippets, where the inline
  `From: … Date: … Subject: …` run survives; the subject strip carries most
  of the signal, so the score was comfortably above the bar anyway.

## Known caveats

- The FastAPI job store (`_jobs` in `api/main.py`) is still in-process memory,
  but no longer load-bearing. A restart loses the live step-by-step *log* of a
  run; it no longer loses the run itself -- `audit.run_results` persists the
  drafted letter and reply metadata, and `/result` rebuilds everything else
  from `out/<request_id>/`. Still single-process: a run genuinely in flight
  when the process dies is not resumed, and more than one worker would need a
  shared store for the live status.
- `letter.draft_and_validate()` can still return `needs_human_review: true`
  on either draft attempt -- this is by design (validator catches it, UI
  surfaces it), not a bug, but it means a run isn't guaranteed a clean letter
  on the first try.
- Minor cosmetic nit, not fixed: the letter sometimes phrases CALL_FREQUENCY
  severity as "the lowest of the three severity tiers" instead of plainly
  "MINOR". Low priority.
- `.env` holds the real Sarvam key and is gitignored; `.env.example` is the
  template committed instead.
