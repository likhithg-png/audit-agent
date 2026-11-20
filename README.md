# Audit Evidence Pack Agent (CO-04)

For each audit request, the agent returns a verdict per account and check: EVIDENCED, GAP, CANNOT_DETERMINE or NOT_APPLICABLE. Every verdict cites records, and gaps are graded on the request's own severity scale.

Design: **the LLM plans and writes; deterministic code decides verdicts.**

Data lives in **Postgres** (via Docker), LLM calls go through **Sarvam's
gateway** (Deepseek / Sarvam / Sarvam Prism), and the UI is **React +
FastAPI**. See [STATUS.md](STATUS.md) for the full build record, caveats, and
what each piece does.

## 1. Prerequisites

- Python 3.11+
- Node 18+ (for the React frontend)
- Docker + Docker Compose (for Postgres)
- A Sarvam API key (`SARVAM_API_KEY`)

## 2. One-time setup

```bash
cd audit-agent
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set SARVAM_API_KEY. DATABASE_URL already points at the
# docker-compose Postgres (localhost:55433) -- only change it if you're
# running Postgres elsewhere.
```

```bash
cd frontend
npm install
cd ..
```

## 3. Start Postgres and load the data

```bash
docker compose up -d
# wait for it to report healthy:
docker compose ps
```

Load the CSVs into Postgres (idempotent -- safe to re-run after a data refresh):
```bash
source .venv/bin/activate
python -m scripts.load_to_postgres
```

## 4. Run the backend + frontend (the primary UI)

**One command, once setup (steps 1-3) is done:**
```bash
./start.sh
```
Brings up Postgres (if it isn't already), then the API and frontend together.
Ctrl+C stops the API and frontend; Postgres is left running since it's a real
database, not a dev-reload process (`docker compose down` to stop that too).

Or run the two pieces separately (useful for watching one's logs on its own,
or restarting just one):

In one terminal:
```bash
source .venv/bin/activate
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

In another terminal:
```bash
cd frontend
npm run dev -- --host 0.0.0.0 --port 5173
```

Open **http://localhost:5173**. The Vite dev server proxies `/api/*` to
`localhost:8000` (see `frontend/vite.config.js`) -- no extra config needed.

The UI has three tabs, all converging on the same pipeline:
- **Sample requests** -- the 3 canned REQ-01/02/03 demo requests.
- **Upload your own** -- a PDF file or pasted email text, plus which
  lender's sample to test against.
- **Gmail inbox** -- a real mailbox, connected once via OAuth (see
  "Gmail inbox setup" below). Off by default until you configure it.

### FastAPI endpoints (if you want to hit the API directly)

| Method | Path | What |
|---|---|---|
| GET | `/api/requests` | sample request catalogue |
| GET | `/api/requests/{id}/text` | raw request text (email/PDF extracted) |
| POST | `/api/requests/{id}/run` | kick off decompose -> run_checks -> pack -> letter in the background |
| GET | `/api/lenders` | which lenders have real sampled-account data to test against |
| POST | `/api/custom/run` | same pipeline, fed by an uploaded PDF (`file`) or pasted text (`text`) + `lender_id`/`period_from`/`period_to` |
| GET | `/api/inbox/status` | `{configured, connected}` -- whether Gmail OAuth is set up and a mailbox is linked |
| GET | `/api/inbox/auth-url` | Google's OAuth consent URL -- navigate the browser there to connect |
| GET | `/api/inbox/messages` | candidate audit-request emails from the connected mailbox (subject/from/date/snippet) |
| POST | `/api/inbox/messages/{message_id}/run` | run the pipeline on one specific email's content |
| POST | `/api/requests/{id}/send-reply` | sends `body` (text) back to the original sender -- Gmail-sourced runs only, one send per request_id, never called automatically |
| GET | `/api/requests/{id}/status` | poll: status + step log |
| GET | `/api/requests/{id}/result` | checks, summary, letter, score (once status is `done`) |
| GET | `/api/requests/{id}/gaps` | gap_items, optional `?check_type=` / `?severity=` filters |
| GET | `/api/requests/{id}/download/{kind}` | `gap_report` / `request_interpretation` / `letter` file |

Custom/Gmail runs get a generated `request_id` (`CUSTOM-xxxxxxxx` /
`GMAIL-<message_id>`) returned from their start call -- use that id for the
shared status/result/gaps/download endpoints above.

## 5. CLI pipeline (optional -- useful for batch runs or CI)

Run the checks for REQ-01 to REQ-03 using `eval/hand_checks/*.json`, writing to `out/<REQ>/` and to Postgres:
```bash
python -m agent.run_checks --all
```

Score `out/` against `data/ground_truth/`:
```bash
python -m eval.score
```

Or run the checks and score in one go:
```bash
python -m eval.score --run
```

Run the trap unit tests:
```bash
pytest -q
```

Each request writes three files to `out/<REQ>/`:
- `verdicts.csv`: one row per account and check, with evidence IDs and rule references.
- `gap_items.csv`: one row per gap (never merged), with record IDs and severity.
- `summary.csv`: counts per check and status.

...and is additively persisted to Postgres (`audit.runs` / `audit.verdicts` / `audit.gap_items`) under its own `run_id`.

To drive the LLM steps directly instead of the hand-written checks, see
`agent/decompose.decompose()` -- the FastAPI `/run` endpoint
(`api/main.py:_execute`) and the legacy `app.py` both show the full
decompose -> run_checks -> pack -> letter wiring end to end.

## 6. Gmail inbox setup (optional)

The "Gmail inbox" tab lists real audit-request emails from a mailbox you
connect, and lets you run the agent on one of them. It's off by default
(`/api/inbox/status` reports `configured: false`) until you create a Google
OAuth client -- this part requires your own Google login, so it can't be
scripted for you:

1. Go to [console.cloud.google.com](https://console.cloud.google.com), create
   (or pick) a project.
2. **APIs & Services -> Library** -> enable the **Gmail API**.
3. **APIs & Services -> OAuth consent screen** -> External (or Internal if
   you're on a Workspace domain) -> fill in the required fields -> add your
   own Google account as a test user if it stays in "Testing" status.
4. **APIs & Services -> Credentials -> Create Credentials -> OAuth client ID**
   -> Application type **Web application** -> under **Authorized redirect
   URIs** add exactly:
   ```
   http://localhost:8000/api/inbox/oauth/callback
   ```
5. Copy the generated **Client ID** and **Client secret** into `.env`:
   ```bash
   GOOGLE_CLIENT_ID=...
   GOOGLE_CLIENT_SECRET=...
   ```
6. Restart the API server (`uvicorn api.main:app --reload ...` -- `--reload`
   picks up the new env vars on the next file change, or just stop/start it).
7. In the UI, open the **Gmail inbox** tab -> **Connect Gmail** -> sign in
   and grant access in your own browser (this app requests `gmail.readonly`
   to list/read messages, and `gmail.send` so a human can send the drafted
   reply -- it never deletes, archives, or auto-sends anything) -> you're
   redirected back to the app.
8. Click **Refresh inbox** to list candidate emails (matches
   `GMAIL_SEARCH_QUERY`, default: any incoming mail within the last 180 days
   -- no subject keyword required since Step 15; Prism classifies each one as
   a real audit request or not, so a genuine request with a blank/off-topic
   subject still gets caught), pick one, choose which lender's sample to test
   against, and run the agent. Each email gets a status chip (New ->
   Processing -> Needs review -> Replied, or Error) that persists across
   refreshes -- an email you've already run or replied to no longer looks
   identical to one you haven't. A newly-seen email also gets a one-time
   Prism confidence caption ("82% likely an audit request") -- informational
   only, it never hides or filters anything.
9. Once the run finishes, a **Send reply** section appears showing the
   original sender's address and the drafted letter, editable. Nothing is
   ever sent automatically -- you review (and can edit) the text, then click
   Send and confirm. One reply per run; the reply threads into the original
   email via Gmail's `In-Reply-To`/thread headers.

Note: "Refresh inbox" always re-runs the search fresh -- there's no
"already processed" tracking, so an email you've already run (or replied to)
can still reappear in the list on the next refresh.

## 7. Legacy Streamlit demo (superseded, still runnable)

```bash
streamlit run app.py
```

This was the original demo UI before the React + FastAPI move. Kept for
reference; the primary UI is now the one in step 4.

## Environment variables

| Variable | Default | What |
|---|---|---|
| `SARVAM_API_KEY` | (none -- required) | Sarvam gateway key |
| `SARVAM_BASE_URL` | `https://api.sarvam.ai/v2` | Sarvam API base |
| `SARVAM_MODEL_REASONING` | `deepseekv4.1-flash-1017` | decompose, letter drafting |
| `SARVAM_MODEL_CONVERSATIONAL` | `sarvam-105b-conversations` | transcript labelling |
| `SARVAM_MODEL_DECISION` | `sarvam-prism` | structured decision scoring (`/v2/systemone`) |
| `DATABASE_URL` | `postgresql://audit:audit@localhost:55433/audit` | Postgres connection |
| `AUDIT_DATA_DIR` | `./data` | source CSVs |
| `AUDIT_OUT_DIR` | `./out` | per-request output (verdicts/gaps/summary/pack/letter) |
| `GOOGLE_CLIENT_ID` | (none) | Gmail OAuth client id -- see "Gmail inbox setup" |
| `GOOGLE_CLIENT_SECRET` | (none) | Gmail OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `http://localhost:8000/api/inbox/oauth/callback` | must match the redirect URI registered in Google Cloud Console |
| `FRONTEND_URL` | `http://localhost:5173` | where the OAuth callback redirects back to after connecting |
| `GMAIL_SEARCH_QUERY` | `newer_than:180d` | Gmail search used to list candidate emails -- no subject keyword filter since Step 15, Prism classifies instead |

## Layout
```
docker-compose.yml             Postgres 16 (port 55433 -- picked to avoid colliding with other local containers)
sql/001_schema.sql             audit schema: 10 source-mirror tables + 4 generated-data tables (runs/verdicts/gap_items/transcript_labels)
sql/002_gmail.sql              audit.oauth_tokens: one row per provider, holds the Gmail refresh token
sql/005_inbox_runs.sql         audit.inbox_runs: persistent NEW/PROCESSING/NEEDS_REVIEW/ERROR/REPLIED status per Gmail message
agent/inbox_runs.py            Step 11: Gmail inbox status lifecycle + one-time Prism classification per message
scripts/load_to_postgres.py    idempotent CSV -> Postgres loader
config/source_registry.yaml    sources, keys, and the time zone of every timestamp (D4 = IST; D5, D10 = UTC)
agent/db.py                    psycopg connection helper
agent/tools.py                 read-only evidence tools: UTC conversion, collector-ID normalization, de-duplication (_raw() is the only Postgres-aware method)
agent/rules.py                 D12 rules (effective dates, strictest applies) and the business-day calendar
agent/verdict.py               Verdict class: EVIDENCED without citations raises an error
agent/checks.py                the 8 deterministic checks
agent/severity.py              grades gaps on the request's scale
agent/run_checks.py            runner for stages 3-5; also persists each run to Postgres
agent/llm.py                   thin Sarvam client (reasoning / conversational / decision)
agent/decompose.py             Step 5: request text/PDF -> checks.json (Deepseek via Sarvam)
agent/transcript_review.py     Step 6: transcript -> outcome labels (Sarvam conversational), cached in audit.transcript_labels
agent/pack.py                  Step 7a: gap_report.xlsx + request_interpretation.md
agent/letter.py                Step 7b: draft response letter from a summary only
agent/validator.py             Step 7c: softening language / PII / disclaimer / gap-mention checks
agent/gmail_client.py          Step 10: Gmail OAuth + message listing/extraction (gmail.readonly)
api/main.py                    Step 9/10: FastAPI backend (background jobs, status polling, downloads, upload + Gmail inbox routes)
frontend/                      Step 9/10: React + Vite UI (sample/upload/Gmail tabs, checks, summary, gap table + evidence drawer, scorecard, letter, downloads)
app.py                         Step 8: Streamlit demo (superseded by api/ + frontend/)
eval/hand_checks/REQ-0X.json   hand-written checks.json (what the LLM decomposer must produce)
eval/score.py                  recall, false-gap rate, unsupported claims, severity accuracy
data/                          synthetic data and ground truth (regenerate: python scripts/generate_data.py)
```

## Status

See [STATUS.md](STATUS.md) for the full build record (all 9 steps, what was
verified, and known caveats). Short version: all steps are done, and the
measured metrics on REQ-01/02/03 are 100% gap recall, 1.7% false-gap rate, 0
unsupported claims.

- Caveat carried over from the original engine: the synthetic answer key
  applies the same rule readings as this engine, so 100% recall here proves
  the code is wired correctly, not that it is robust against real-world data
  that reads the rules differently. Re-check every assumption in
  `source_registry.yaml` against the organizers' real data.
