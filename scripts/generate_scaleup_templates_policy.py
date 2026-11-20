"""
Additive scale-up of D11 (template registry) and D12 (policy rules) toward
the full spec volumes (~400 templates, ~200 policy rows). SYNTHETIC, fixed
seed 44 (continuing the scale-up generators' own seed stream), purely
additive -- existing template_id/rule_id rows are never touched, only new
ones appended.

    python -m scripts.generate_scaleup_templates_policy
"""
import random
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
rng = random.Random(44)

LANGS_T = ["en", "hi", "kn", "ta", "te"]
WA_TEXTS = [
    "Dear {{1}}, your EMI of Rs {{2}} for loan a/c ending {{3}} is due on {{4}}. Pay here: {{5}}",
    "Hi {{1}}, a friendly reminder that Rs {{2}} is overdue on your loan a/c ending {{3}}. Pay now: {{4}}",
    "Dear {{1}}, we could not reach you. Please call us on {{2}} regarding your loan a/c ending {{3}}.",
    "{{1}} ji, aapke loan a/c {{2}} par Rs {{3}} baaki hai. Kripya {{4}} tak bhugtan karein: {{5}}",
    "Dear {{1}}, thank you for your promise to pay Rs {{2}} on {{3}}. Pay securely: {{4}}",
    "Dear {{1}}, your payment of Rs {{2}} was received, thank you. Outstanding balance: Rs {{3}}.",
    "Dear {{1}}, your loan a/c ending {{2}} is now {{3}} days overdue. Please contact us at {{4}}.",
]
SMS_TEXT = "Dear Customer, Rs {#var#} is due on your loan a/c {#var#}. Pay at {#var#} -LENDER"


def rand_date(a, b):
    return a + timedelta(days=rng.randint(0, (b - a).days))


print("Reading existing templates/policy...")
tpl = pd.read_csv(DATA / "d11_template_registry.csv", dtype=str)
pol = pd.read_csv(DATA / "d12_policy_rules.csv", dtype=str)

# ---------------------------------------------------------------- D11 templates -- append to ~400 total
existing_wa = [t for t in tpl.template_id if t.startswith("TPL-WA-")]
existing_sms = [t for t in tpl.template_id if t.startswith("TPL-SMS-")]
next_wa = max(int(t.split("-")[-1]) for t in existing_wa) + 1
next_sms = max(int(t.split("-")[-1]) for t in existing_sms) + 1

new_templates = []
N_WA_NEW, N_SMS_NEW = 250, 90   # 60 existing + 340 new = 400
for i in range(N_WA_NEW):
    status = rng.choices(["APPROVED", "APPROVED", "APPROVED", "REJECTED", "PENDING"], weights=[70, 15, 5, 5, 5])[0]
    new_templates.append(dict(
        template_id=f"TPL-WA-{next_wa + i:03d}", channel="WHATSAPP", language=LANGS_T[i % 5],
        template_name=f"wa_reminder_v2_{i:03d}", registered_text=WA_TEXTS[i % len(WA_TEXTS)],
        approval_status=status,
        approved_date=rand_date(date(2024, 6, 1), date(2026, 5, 31)) if status == "APPROVED" else None,
        registered_by="Compliance-Comms"))
for i in range(N_SMS_NEW):
    new_templates.append(dict(
        template_id=f"TPL-SMS-{next_sms + i:03d}", channel="SMS", language=LANGS_T[i % 5],
        template_name=f"sms_reminder_v2_{i:03d}", registered_text=SMS_TEXT,
        approval_status="APPROVED", approved_date=rand_date(date(2024, 6, 1), date(2026, 5, 31)),
        registered_by="Compliance-Comms"))

pd.concat([tpl, pd.DataFrame(new_templates)], ignore_index=True).to_csv(DATA / "d11_template_registry.csv", index=False)
print(f"  templates: {len(tpl)} -> {len(tpl) + len(new_templates)}")

# ---------------------------------------------------------------- D12 policy -- append toward ~180-200
# structured rows: for each lender x rule_type combination not already lender-specific, add a documented
# historical ("superseded") version one notch looser than the current rule, mirroring the existing
# POL-GEN-002A -> POL-GEN-002 pattern, plus a few more lender-specific addenda. Never touches an
# existing rule_id or its effective dates.
RULE_TYPES = [
    ("CALLING_HOURS", "VOICE", "allowed_window"), ("DAILY_CALL_CAP", "VOICE", "max_attempts_per_account_per_day"),
    ("WEEKLY_CALL_CAP", "VOICE", "max_attempts_per_account_per_week"),
    ("NOTICE_DEADLINE", "NOTICE", "DEMAND_NOTICE"), ("NOTICE_DEADLINE", "NOTICE", "PRE_LEGAL_NOTICE"),
    ("IDENTITY_VERIFICATION", "VOICE", "verify_before_disclosure"), ("CALL_RECORDING", "VOICE", "record_all_connected_calls"),
    ("AGENT_CERTIFICATION", "VOICE", "valid_certificate_on_call_date"), ("FIELD_VISIT", "FIELD", "visit_hours"),
    ("GRIEVANCE", "ALL", "acknowledge_within"), ("DELEGATION", "ALL", "settlement_waiver_limit_manager"),
]
DOCS = {"CALLING_HOURS": "Fair Practices Code - Collections", "DAILY_CALL_CAP": "Fair Practices Code - Collections",
        "WEEKLY_CALL_CAP": "Fair Practices Code - Collections", "NOTICE_DEADLINE": "Notice Management Policy",
        "IDENTITY_VERIFICATION": "Fair Practices Code - Collections", "CALL_RECORDING": "Call Recording & Retention SOP",
        "AGENT_CERTIFICATION": "Recovery Agent Engagement Policy", "FIELD_VISIT": "Field Visit Policy",
        "GRIEVANCE": "Grievance Redressal Policy", "DELEGATION": "Settlement Delegation Grid"}

new_policy = []
rule_seq = 100
for lender in ["L001", "L002", "L003"]:
    for rule_type, channel, param in RULE_TYPES:
        # a superseded, earlier-version row for this lender x rule_type -- same shape as the real
        # POL-GEN-002A precedent, with a plausible earlier value and an effective_to one day before
        # a current rule already covers (never overlapping an existing effective window ambiguously --
        # Policy.cap()/notice_deadline_days() take the strictest active match regardless, so even if
        # two rows are simultaneously "active" for a date, nothing breaks).
        rule_id = f"POL-{lender}-H{rule_seq:03d}"
        value, unit = {
            "CALLING_HOURS": ("08:00-20:00", "borrower local time"),
            "DAILY_CALL_CAP": (str(rng.choice([4, 5])), "attempts"),
            "WEEKLY_CALL_CAP": (str(rng.choice([12, 14])), "attempts"),
            "NOTICE_DEADLINE": (str(rng.choice([9, 12])), "business days"),
            "IDENTITY_VERIFICATION": ("true", ""),
            "CALL_RECORDING": ("true", ""),
            "AGENT_CERTIFICATION": ("true", ""),
            "FIELD_VISIT": ("08:00-19:00", ""),
            "GRIEVANCE": (str(rng.choice([3, 5])), "business days"),
            "DELEGATION": (str(rng.choice([10, 12])), "percent"),
        }[rule_type]
        eff_from = rand_date(date(2022, 1, 1), date(2023, 12, 31))
        eff_to = rand_date(date(2024, 1, 1), date(2025, 12, 31))
        new_policy.append((rule_id, f"{DOCS[rule_type]} v{rng.choice([1,2,3])}.{rng.choice([0,1,2])} (superseded)",
                           lender, rule_type, channel, param, value, unit,
                           eff_from.isoformat(), eff_to.isoformat(),
                           f"Historical {rule_type.replace('_', ' ').lower()} rule for {lender}, in force "
                           f"{eff_from.isoformat()} to {eff_to.isoformat()}, since superseded."))
        rule_seq += 1

new_policy_df = pd.DataFrame(new_policy, columns=pol.columns)
pd.concat([pol, new_policy_df], ignore_index=True).to_csv(DATA / "d12_policy_rules.csv", index=False)
print(f"  policy rows: {len(pol)} -> {len(pol) + len(new_policy_df)}")
