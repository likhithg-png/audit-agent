import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("AUDIT_DATA_DIR", ROOT / "data"))
REGISTRY = ROOT / "config" / "source_registry.yaml"
OUT_DIR = Path(os.environ.get("AUDIT_OUT_DIR", ROOT / "out"))
HAND_CHECKS = ROOT / "eval" / "hand_checks"
CACHE_DIR = ROOT / "data" / "cache"
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://audit:audit@localhost:55433/audit")
# A real Postgres role with only SELECT grants (sql/008_readonly_role.sql) -- used for any
# SQL text an LLM writes itself (agent/tools.py Tools.sql()), so "read-only" is a database
# permission, not just a string check on the query text.
DATABASE_URL_READONLY = os.environ.get(
    "DATABASE_URL_READONLY", "postgresql://audit_readonly:audit_readonly_ro@localhost:55433/audit"
)

# Sarvam -- hosts Deepseek (generic reasoning) + Sarvam's own conversational model,
# plus a separate structured-decision endpoint (Prism). See agent/llm.py.
SARVAM_API_KEY = os.environ.get("SARVAM_API_KEY", "")
SARVAM_BASE_URL = os.environ.get("SARVAM_BASE_URL", "https://api.sarvam.ai/v2")
SARVAM_MODEL_REASONING = os.environ.get("SARVAM_MODEL_REASONING", "deepseekv4.1-flash-1017")
SARVAM_MODEL_CONVERSATIONAL = os.environ.get("SARVAM_MODEL_CONVERSATIONAL", "sarvam-105b-conversations")
SARVAM_MODEL_DECISION = os.environ.get("SARVAM_MODEL_DECISION", "sarvam-prism")

# Gmail inbox integration (Step 10) -- OAuth client credentials come from a
# Google Cloud project you create yourself (see README.md); this app never
# holds your Google password, only a token your own consent grants it.
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8000/api/inbox/oauth/callback")
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")
# Step 15: was subject:(audit OR compliance OR annexure OR "audit request") -- missed a genuine
# audit request sent with a blank subject (real incident: pannaga.n@creditnirvana.ai's email had
# no subject AND no trigger word anywhere in the body, so no keyword filter could have caught it).
# Widened to just the time window; Prism (agent/inbox_runs.py's _classify()) is the real filter now
# -- it already proved it can tell a genuine request from noise (STATUS.md Step 11: a GitHub
# notification scored 3.3%, a real request scored 71.8-85%). Costs one more Prism call per
# irrelevant email in the inbox, accepted as the tradeoff for not silently missing real requests.
GMAIL_SEARCH_QUERY = os.environ.get(
    "GMAIL_SEARCH_QUERY", "newer_than:180d"
)
