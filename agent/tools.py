"""
Read-only evidence tools (Stage 3).

Every tool:
  * reads one source system (as declared in config/source_registry.yaml),
  * converts every timestamp to UTC ONCE, here, using the registry's time zone,
  * also adds the borrower-local time where the check needs it,
  * returns record IDs so every verdict can cite them.

Later these same functions become the LLM agent's tools (warehouse SQL, contact log,
transcript store, consent registry, template registry, dispatch tracker, cert register).
"""
import re
from functools import lru_cache

import pandas as pd
import yaml

from . import db
from .config import REGISTRY


def normalize_collector_id(raw):
    """'COL-0042', 'col42', 'COL0042' -> 'COL-0042'. Dialers of different agencies use different formats."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    m = re.search(r"(\d+)", raw)
    return f"COL-{int(m.group(1)):04d}" if m else raw.strip().upper()


class Tools:
    def __init__(self, registry=REGISTRY):
        self.reg = yaml.safe_load(open(registry))
        self.notes = []            # data-quality notes surfaced in the evidence pack

    # ------------------------------------------------------------------ helpers
    def _src(self, name):
        return self.reg["sources"][name]

    @lru_cache(maxsize=None)
    def _raw(self, name):
        """The ONLY thing that changed in this migration -- every method below
        reads through this one method, so swapping CSV -> Postgres here is the
        entire data-layer migration. Everything downstream is untouched."""
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT * FROM audit.{name}")
            rows = cur.fetchall()
        return pd.DataFrame(rows)

    def _to_utc(self, s, tz):
        return pd.to_datetime(s).dt.tz_localize(tz).dt.tz_convert("UTC")

    def borrower_tz(self, state):
        return self.reg.get("state_timezones", {}).get(state, self.reg["default_borrower_timezone"])

    # ------------------------------------------------------------------ D1
    @lru_cache(maxsize=None)
    def accounts(self):
        df = self._raw("accounts").copy()
        df["tz"] = df["state"].map(self.borrower_tz)
        return df.set_index("account_id", drop=False)

    # ------------------------------------------------------------------ D4 contact log
    @lru_cache(maxsize=None)
    def _contacts_all(self):
        df = self._raw("contact_log").copy()
        before = len(df)
        df = df.drop_duplicates("contact_id")
        if len(df) < before:
            self.notes.append(f"D4: {before - len(df)} exact duplicate contact rows ignored (same contact_id).")
        tz = self._src("contact_log")["timestamps"]["contact_ts"]
        df["ts_raw"] = df["contact_ts"]
        df["ts_utc"] = self._to_utc(df["contact_ts"], tz)
        acc = self.accounts()
        df["lender_id"] = df["account_id"].map(acc["lender_id"])
        df["borrower_id"] = df["account_id"].map(acc["borrower_id"])
        # borrower-local time (all IST today, but done properly per account)
        tzs = df["account_id"].map(acc["tz"])
        df["ts_local"] = [t.tz_convert(z).tz_localize(None) for t, z in zip(df["ts_utc"], tzs)]
        df["local_date"] = df["ts_local"].dt.date
        df["collector_id_raw"] = df["collector_id"]
        df["collector_id"] = df["collector_id_raw"].map(normalize_collector_id)
        df["duration_sec"] = df["duration_sec"].fillna("0").astype(int)
        return df

    def contact_log(self, account_ids, channel=None, start=None, end=None):
        df = self._contacts_all()
        df = df[df["account_id"].isin(set(account_ids))]
        if channel:
            df = df[df["channel"] == channel]
        if start:
            df = df[df["local_date"] >= start]
        if end:
            df = df[df["local_date"] <= end]
        return df.copy()

    # ------------------------------------------------------------------ D5 transcripts
    @lru_cache(maxsize=None)
    def _transcripts_all(self):
        df = self._raw("transcripts").copy()
        df["turn_no"] = df["turn_no"].astype(int)
        df["asr_confidence"] = df["asr_confidence"].astype(float)
        df["ts_utc"] = self._to_utc(df["turn_start_ts"], self._src("transcripts")["timestamps"]["turn_start_ts"])
        return df.sort_values(["call_id", "turn_no"])

    def transcripts(self, call_ids):
        df = self._transcripts_all()
        return df[df["call_id"].isin(set(call_ids))].copy()

    # ------------------------------------------------------------------ D10 consent
    @lru_cache(maxsize=None)
    def _consent_all(self):
        df = self._raw("consent").copy()
        tz = self._src("consent")["timestamps"]
        df["captured_utc"] = self._to_utc(df["captured_at"], tz["captured_at"])
        df["opt_out_utc"] = self._to_utc(df["opt_out_at"], tz["opt_out_at"])
        return df

    def consent(self, borrower_ids, channel=None):
        df = self._consent_all()
        df = df[df["borrower_id"].isin(set(borrower_ids))]
        if channel:
            df = df[df["channel"] == channel]
        return df.copy()

    # ------------------------------------------------------------------ D11 / D12
    @lru_cache(maxsize=None)
    def templates(self):
        df = self._raw("templates").copy()
        df["approved_date"] = pd.to_datetime(df["approved_date"]).dt.date
        return df.set_index("template_id", drop=False)

    @lru_cache(maxsize=None)
    def policy(self):
        df = self._raw("policy").copy()
        df["effective_from"] = pd.to_datetime(df["effective_from"]).dt.date
        df["effective_to"] = pd.to_datetime(df["effective_to"]).dt.date
        return df

    # ------------------------------------------------------------------ notices / holidays / collectors
    def notices(self, account_ids, trigger_from=None, trigger_to=None):
        df = self._raw("notices").copy()
        df = df[df["account_id"].isin(set(account_ids))]
        for c in ["trigger_date", "dispatch_date", "service_date"]:
            df[c] = pd.to_datetime(df[c]).dt.date
        if trigger_from:
            df = df[df["trigger_date"] >= trigger_from]
        if trigger_to:
            df = df[df["trigger_date"] <= trigger_to]
        return df

    @lru_cache(maxsize=None)
    def holidays(self):
        df = self._raw("holidays").copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date
        return df

    @lru_cache(maxsize=None)
    def roster(self):
        return self._raw("roster").set_index("collector_id", drop=False)

    @lru_cache(maxsize=None)
    def certificates(self):
        df = self._raw("certificates").copy()
        df["valid_from"] = pd.to_datetime(df["valid_from"]).dt.date
        df["valid_to"] = pd.to_datetime(df["valid_to"]).dt.date
        return df

    # ------------------------------------------------------------------ guideline-check tables
    def extra(self, name):
        """caller_numbers, message_content, call_conduct_log, field_visits, closures, penal_charges."""
        return self._raw(name).copy()

    # ------------------------------------------------------------------ warehouse SQL (read-only)
    def sql(self, query: str) -> pd.DataFrame:
        """Read-only SQL straight against Postgres, for LLM-written query text
        (agent/decompose_agent.py's run_sql tool). Tables are schema-qualified:
        audit.accounts, audit.contact_log, audit.transcripts, audit.consent,
        audit.templates, audit.policy, audit.notices, audit.holidays,
        audit.roster, audit.certificates.

        Read-only is enforced twice: this prefix/keyword check is a cheap first
        pass that rejects the obvious cases with a clear message instead of a
        raw Postgres error, but the real boundary is connect_readonly() below --
        audit_readonly (sql/008_readonly_role.sql) has no INSERT/UPDATE/DELETE/DDL
        grant on anything, so a query that slips past this string check still
        can't write."""
        q = query.strip().lower()
        if not (q.startswith("select") or q.startswith("with")) or any(
                w in q for w in [" insert ", " update ", " delete ", " drop ", " create ", " alter ", " copy "]):
            raise PermissionError("Only read-only SELECT queries are allowed")
        with db.connect_readonly() as conn, conn.cursor() as cur:
            cur.execute(query)
            rows = cur.fetchall()
        return pd.DataFrame(rows)
