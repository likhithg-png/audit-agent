"""
Human-readable account results for the UI: one row per account x check, with
the channel, a plain-English reason, and the underlying contacts / consent
records -- so nobody has to open the database to understand a verdict.
"""
import json
from datetime import date
from functools import lru_cache

import pandas as pd

from .config import OUT_DIR
from .tools import Tools

CHANNEL_NOUN = {"WHATSAPP": "WhatsApp message", "SMS": "SMS", "VOICE": "call"}
CHANNEL_LABEL = {"WHATSAPP": "WhatsApp", "SMS": "SMS", "VOICE": "Voice call"}
RECORD_NOUN = {"FIELD_VISIT": "visit", "SECURITY_RELEASE": "loan closure",
               "PENAL_CHARGES": "penal charge", "MESSAGE_CONTENT": "message", "CALL_CONDUCT": "call",
               "CALLER_NUMBER_SERIES": "call"}
GAP_TEXT = {
    "visit_outside_hours": "visit outside the permitted hours",
    "visit_not_logged": "visit not logged in the field app",
    "third_party_disclosure": "dues disclosed to a third party",
    "security_not_released": "security not released after closure",
    "penal_capitalised": "penal charge capitalised",
    "penal_on_penal": "penal charge levied on penal charges",
    "penal_not_disclosed_in_reminder": "reminder did not state the penal charge",
    "inappropriate_message": "threatening or inappropriate message",
    "abusive_or_threatening_language": "abusive or threatening language",
    "non_160_caller": "call not from the 160-series number",
    "no_consent_record": "no consent on file",
    "message_after_opt_out": "sent after the borrower opted out",
    "consent_after_message": "consent captured only after the contact",
}


# Check types that only ever look at one channel (consent is the only one that takes it as a parameter).
FIXED_CHANNEL = {"TEMPLATE_APPROVED": "WHATSAPP", "CALL_HOURS": "VOICE", "CALL_FREQUENCY": "VOICE",
                 "AGENT_CERTIFICATION": "VOICE", "VERIFICATION_BEFORE_DISCLOSURE": "VOICE", "CALL_RECORDING": "VOICE",
                 "CALLER_NUMBER_SERIES": "VOICE", "CALL_CONDUCT": "VOICE"}
CHECK_NAME = {"TEMPLATE_APPROVED": "Approved template", "CALL_HOURS": "Call timing", "CALL_FREQUENCY": "Call frequency",
              "AGENT_CERTIFICATION": "Agent certification", "VERIFICATION_BEFORE_DISCLOSURE": "Identity verification",
              "CALL_RECORDING": "Call recording", "NOTICE_TIMELINE": "Notice timeline",
              "CONSENT_BEFORE_CONTACT": "Consent", "CALLER_NUMBER_SERIES": "Caller number (160 series)",
              "MESSAGE_CONTENT": "Message content", "CALL_CONDUCT": "Call conduct", "FIELD_VISIT": "Field visit",
              "SECURITY_RELEASE": "Security release",
              "PENAL_CHARGES": "Penal charges"}


@lru_cache(maxsize=1)
def _tools():
    return Tools()


def _plural(n, noun):
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _fmt(ts):
    return "" if pd.isna(ts) else f"{ts:%Y-%m-%d %H:%M} UTC"


def _consent_rows(c):
    return [dict(consent_id=r.consent_id, status=r.consent_status, source=r.consent_source,
                 captured=_fmt(r.captured_utc), opted_out=_fmt(r.opt_out_utc) or "-")
            for r in c.sort_values("captured_utc").itertuples()]


def _consent_explain(tools, chk, v, gaps, period):
    channel = chk["params"].get("channel")
    noun = CHANNEL_NOUN.get(channel, "contact")
    label = CHANNEL_LABEL.get(channel, channel or "")
    acct = v["account_id"]
    acc = tools.accounts()
    borrower = acc.at[acct, "borrower_id"]
    contacts = tools.contact_log([acct], channel, period[0], period[1]).sort_values("ts_utc")
    consent = tools.consent([borrower], channel)
    span = f"{period[0]:%d %b %Y} and {period[1]:%d %b %Y}"
    n = len(contacts)
    gap_by_contact = {}
    for g in gaps:
        for rid in str(g["record_ids"]).split(";"):
            if rid.startswith("CT"):
                gap_by_contact[rid] = g
    consent_line = (f"{_plural(len(consent), 'consent record')} on file for this borrower"
                    if len(consent) else "no consent record on file for this borrower")

    if v["status"] == "NOT_APPLICABLE":
        headline = f"No {label} contact in the period"
        if len(consent):
            summary = (f"There were 0 {label} contacts between {span}, so there is nothing to test. "
                       f"{label} consent exists ({consent_line}), but consent only matters when the borrower is contacted.")
        else:
            summary = (f"There were 0 {label} contacts between {span}, so there is nothing to test. "
                       f"There is also {consent_line}.")
    elif v["status"] == "GAP":
        bad = len(gap_by_contact) or len(gaps)
        kinds = sorted({GAP_TEXT.get(g["gap_type"], g["gap_type"]) for g in gaps})
        headline = f"{bad} of {_plural(n, noun)} without valid consent"
        summary = (f"{_plural(n, noun)} made between {span}; {bad} had a consent problem "
                   f"({'; '.join(kinds)}). Consent check: {consent_line}.")
    elif v["status"] == "EVIDENCED":
        headline = (f"The {noun} had valid consent" if n == 1 else f"All {_plural(n, noun)} had valid consent")
        summary = (f"{_plural(n, noun)} made between {span}. Valid {label} consent was on file before "
                   f"{'it' if n == 1 else 'each'} (not opted out; {consent_line}).")
    else:
        headline, summary = "Cannot determine", v.get("cannot_determine_reasons", "")

    items = []
    for r in contacts.itertuples():
        g = gap_by_contact.get(r.contact_id)
        items.append(dict(contact_id=r.contact_id, time=_fmt(r.ts_utc), outcome=r.disposition,
                          result=("GAP: " + GAP_TEXT.get(g["gap_type"], g["gap_type"])) if g else "OK"))
    return headline, summary, items, _consent_rows(consent)


CD_TEXT = {
    "transcript_missing": "no call transcript or recording on file",
    "label_failed": "the AI could not reliably tell whether the identity check came first",
    "collector_not_in_roster": "the caller is not in the collector roster",
}


def _cd_reasons(codes) -> str:
    return "; ".join(CD_TEXT.get(c, c.replace("_", " ")) for c in sorted({c for c in codes if c}))


CD_MEANING = ("Cannot determine means the data needed to say pass or fail is missing, so no verdict is given "
              "(it is not counted as a pass or a fail).")


def account_results(request_id: str):
    od = OUT_DIR / request_id
    spec = json.load(open(od / "checks.json"))
    checks = {c["check_id"]: c for c in spec["checks"]}
    period = (date.fromisoformat(spec["period"]["from"]), date.fromisoformat(spec["period"]["to"]))
    vdf = pd.read_csv(od / "verdicts.csv", dtype=str).fillna("")
    gdf = pd.read_csv(od / "gap_items.csv", dtype=str).fillna("") if (od / "gap_items.csv").exists() else pd.DataFrame()
    udf = (pd.read_csv(od / "undetermined_items.csv", dtype=str).fillna("")
           if (od / "undetermined_items.csv").exists() else pd.DataFrame())
    tools = _tools()
    out = []
    for v in vdf.to_dict("records"):
        chk = checks.get(v["check_id"], {})
        channel = chk.get("params", {}).get("channel") or FIXED_CHANNEL.get(v["check_type"], "")
        gaps = gdf[(gdf.account_id == v["account_id"]) & (gdf.check_id == v["check_id"])].to_dict("records") if len(gdf) else []
        und = udf[(udf.account_id == v["account_id"]) & (udf.check_id == v["check_id"])].to_dict("records") if len(udf) else []
        row = dict(v, channel=channel, check_name=CHECK_NAME.get(v["check_type"], v["check_type"]), channel_label=CHANNEL_LABEL.get(channel, channel),
                   test=chk.get("test", ""), period=f"{period[0]} to {period[1]}")
        if v["check_type"] == "CONSENT_BEFORE_CONTACT":
            try:
                row["headline"], row["summary"], row["items"], row["consent"] = _consent_explain(tools, chk, v, gaps, period)
                if v["status"] == "CANNOT_DETERMINE":
                    why = _cd_reasons([u["reason"] for u in und] or v.get("cannot_determine_reasons", "").split(";"))
                    row["headline"] = f"Could not be tested: {why}"
                    row["summary"] = f"{row['summary'] and row['summary'] + ' '}{CD_MEANING}"
            except Exception as e:  # never break the whole table over one explanation
                row.update(headline=v["status"], summary=f"(could not build details: {e})", items=[], consent=[])
        else:
            noun = RECORD_NOUN.get(v["check_type"]) or CHANNEL_NOUN.get(channel, "item")
            n = int(v["items_tested"] or 0)
            bad = len({g["record_ids"].split(";")[0] for g in gaps})
            kinds = sorted({GAP_TEXT.get(g["gap_type"], g["gap_type"].replace("_", " ")) for g in gaps})
            span = f"{period[0]:%d %b %Y} and {period[1]:%d %b %Y}"
            rule = chk.get("test", "").rstrip(". ")
            if v["status"] == "GAP":
                row["headline"] = f"{bad} of {_plural(n, noun)} failed: {'; '.join(kinds)}"
                row["summary"] = (f"{_plural(n, noun)} between {span}; {n - bad} passed and {bad} failed. "
                                  f"Rule: {rule}. Failed because: {'; '.join(kinds)}.")
            elif v["status"] == "EVIDENCED":
                row["headline"] = f"All {_plural(n, noun)} passed"
                row["summary"] = f"{_plural(n, noun)} between {span}, all compliant. Rule: {rule}."
            elif v["status"] == "NOT_APPLICABLE":
                row["headline"] = f"No {noun} in the period"
                row["summary"] = f"There was no {noun} between {span}, so there is nothing to test. Rule: {rule}."
            else:
                why = _cd_reasons([u["reason"] for u in und] or v.get("cannot_determine_reasons", "").split(";"))
                k = len({u["record_id"] for u in und})
                if k:
                    row["headline"] = f"{k} of {_plural(n, noun)} could not be tested: {why}"
                    row["summary"] = (f"{_plural(n, noun)} between {span}; {n - k} {'was' if n - k == 1 else 'were'} tested and passed, {k} could not "
                                      f"be tested because {why}. {CD_MEANING} Rule: {rule}.")
                else:
                    row["headline"] = f"Could not be tested: {why or 'data missing'}"
                    row["summary"] = f"{_plural(n, noun)} between {span}. Some could not be tested because {why or 'data was missing'}. {CD_MEANING} Rule: {rule}."
            items = [dict(contact_id=g["record_ids"].split(";")[0], time="", outcome="FAILED: " + GAP_TEXT.get(g["gap_type"], g["gap_type"].replace("_", " ")),
                          result=g["detail"]) for g in gaps]
            items += [dict(contact_id=u["record_id"], time="", outcome="COULD NOT TEST: " + _cd_reasons([u["reason"]]),
                           result=u["detail"]) for u in und]
            failed = {i["contact_id"] for i in items}
            seen = set(failed)
            for e in v["evidence_ids"].split(";"):
                if e and e not in seen and not e.startswith(("POL-", "G-")) and "#" not in e:
                    seen.add(e)
                    items.append(dict(contact_id=e, time="", outcome="Passed", result=""))
            row["items"] = items
            row["consent"] = []
        out.append(row)
    return out


def check_label(chk: dict) -> str:
    """Human name for a check, e.g. 'Consent - WhatsApp' (+ guideline id when it came from the catalogue)."""
    ct = chk.get("check_type", "")
    ch = chk.get("params", {}).get("channel") or FIXED_CHANNEL.get(ct, "")
    label = CHECK_NAME.get(ct, ct)
    if ch:
        label += f" - {CHANNEL_LABEL.get(ch, ch)}"
    sq = chk.get("source_question", "")
    if sq.startswith("G-"):
        label += f" [{sq}]"
    return label
