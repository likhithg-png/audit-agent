"""
Additive scale-up toward the full D1/D4/D5/D10 Agentathon spec volumes
(D1 ~100k accounts, D4 ~3M contact attempts, D5 audio-covered connected
calls, D10 ~85-90k consent registry). SYNTHETIC, fixed seed (44, separate
from generate_data.py's 42 and generate_conduct_data.py's 43), and purely
additive: reads the existing CSVs and APPENDS new rows for 98,000 NEW
accounts/borrowers only, using the EXISTING table schemas only -- no new
tables, no new columns. The original 2,000 accounts and every row derived
from them (contact log, transcripts, consent, REQ-01/02/03 samples, their
ground truth, the 100%/1.7% eval scores) are never modified -- this script
never rewrites an existing row, only adds new ones with a new, non-
colliding ID range (ACC200001+, BRW900001+, CT90000001+, CNS9000001+).

Call-transcript coverage is kept at the same ~100% (of connected calls)
rate as the original generator, not capped at the literal "audio for
2,000" spec line -- an explicit decision (see STATUS.md) to avoid making
most of the new accounts fail Call Recording / Verification checks by
default from missing data rather than a real compliance issue.

    python -m scripts.generate_scaleup_core

Rewrites (appends to, via full read-modify-write since these are plain
CSVs, not append-only files) d1_account_master_thin.csv, d4_contact_history.csv,
d5_call_transcripts.csv, d10_consent_registry.csv -- the original rows in each
are preserved byte-for-byte; only new rows are added.
"""
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
rng = random.Random(44)

IST = timedelta(hours=5, minutes=30)
P_START, P_END = date(2026, 1, 1), date(2026, 6, 30)   # same contact-data period as the original generator

# ---------------------------------------------------------------- reference data (copied from generate_data.py
# for self-containment, same convention generate_conduct_data.py already uses -- must match exactly so new
# accounts/collectors/holidays are consistent with the existing dataset, not a divergent second world)
CITIES = [("Bengaluru", "KA"), ("Mysuru", "KA"), ("Chennai", "TN"), ("Coimbatore", "TN"),
          ("Mumbai", "MH"), ("Pune", "MH"), ("Hyderabad", "TG"), ("New Delhi", "DL"),
          ("Lucknow", "UP"), ("Ahmedabad", "GJ"), ("Kolkata", "WB"), ("Kochi", "KL")]
LANG = {"KA": ["kn", "en", "hi-en"], "TN": ["ta", "en"], "TG": ["te", "en", "hi-en"],
        "MH": ["hi-en", "en"], "DL": ["hi-en", "en"], "UP": ["hi-en"], "GJ": ["hi-en", "en"],
        "WB": ["en", "hi-en"], "KL": ["en"]}
FIRST = ["Ramesh", "Suresh", "Priya", "Lakshmi", "Arjun", "Kavya", "Mohammed", "Fatima", "Rahul",
         "Anjali", "Venkatesh", "Deepa", "Sanjay", "Meena", "Karthik", "Divya", "Imran", "Pooja",
         "Ganesh", "Swati", "Naveen", "Rekha", "Vikram", "Asha", "Harish", "Nandini", "Manoj", "Sunita"]
LAST = ["Kumar", "Reddy", "Sharma", "Iyer", "Patil", "Nair", "Khan", "Gowda", "Das", "Singh",
        "Rao", "Menon", "Joshi", "Shetty", "Pillai", "Verma", "Naidu", "Mehta", "Banerjee", "Shaikh"]
PRODUCTS = ["Personal Loan", "Two Wheeler Loan", "Consumer Durable Loan", "Credit Card", "Business Loan"]
LENDER_AGENCIES = {"L001": ["AG01", "AG02"], "L002": ["AG03", "AG04"], "L003": ["AG05", "AG01"]}
AGENCY_NAMES = {"AG01": "Sahyog Recovery Services", "AG02": "Pragati Collections",
                "AG03": "Vishwas Credit Solutions", "AG04": "Samadhan Field Services",
                "AG05": "Dakshin Recovery Partners"}
# New accounts keep the SAME lender split ratio as the original 700/650/650 (out of 2000).
N_NEW_ACCOUNTS = 98_000
LENDERS_NEW = {"L001": 34_300, "L002": 31_850, "L003": 31_850}

NATIONAL = {
    date(2026, 1, 26): "Republic Day", date(2026, 3, 4): "Holi", date(2026, 3, 20): "Id-ul-Fitr",
    date(2026, 3, 26): "Ram Navami", date(2026, 3, 31): "Mahavir Jayanti", date(2026, 4, 3): "Good Friday",
    date(2026, 4, 14): "Dr. Ambedkar Jayanti", date(2026, 5, 1): "Buddha Purnima",
    date(2026, 5, 27): "Id-ul-Zuha (Bakrid)", date(2026, 6, 26): "Muharram",
}
STATE_HOL = {
    "KA": {date(2026, 3, 19): "Ugadi", date(2026, 5, 4): "Basava Jayanti"},
    "TN": {date(2026, 1, 15): "Pongal", date(2026, 1, 16): "Thiruvalluvar Day", date(2026, 1, 17): "Uzhavar Thirunal",
           date(2026, 4, 15): "Tamil New Year (observed)"},
    "MH": {date(2026, 2, 19): "Chhatrapati Shivaji Maharaj Jayanti", date(2026, 3, 19): "Gudi Padwa",
           date(2026, 5, 1): "Maharashtra Day"},
    "TG": {date(2026, 1, 14): "Bhogi", date(2026, 3, 19): "Ugadi", date(2026, 6, 2): "Telangana Formation Day"},
    "DL": {date(2026, 2, 5): "Delhi local holiday (synthetic)"},
    "UP": {date(2026, 1, 24): "UP Diwas", date(2026, 2, 16): "Maha Shivratri (state)"},
    "GJ": {date(2026, 1, 14): "Uttarayan", date(2026, 5, 1): "Gujarat Sthapna Divas"},
    "WB": {date(2026, 1, 23): "Netaji Jayanti", date(2026, 4, 15): "Bengali New Year", date(2026, 10, 19): "Durga Puja (Saptami)"},
    "KL": {date(2026, 1, 2): "Mannam Jayanti", date(2026, 4, 15): "Vishu (observed)", date(2026, 8, 26): "Thiruvonam"},
}


def rand_date(a, b):
    return a + timedelta(days=rng.randint(0, (b - a).days))


def rand_time(h1, m1, h2, m2):
    s1, s2 = h1 * 3600 + m1 * 60, h2 * 3600 + m2 * 60
    s = rng.randint(s1, s2)
    return time(s // 3600, (s % 3600) // 60, s % 60)


def mask_phone():
    return f"+91-{rng.choice('6789')}XXXXXX{rng.randint(100, 999)}"


def hours_window(lender):
    return (time(9, 0), time(18, 0)) if lender == "L002" else (time(8, 0), time(19, 0))


print("Reading existing CSVs...")
d1 = pd.read_csv(DATA / "d1_account_master_thin.csv", dtype=str)
d4 = pd.read_csv(DATA / "d4_contact_history.csv", dtype=str, keep_default_na=False)
d5 = pd.read_csv(DATA / "d5_call_transcripts.csv", dtype=str, keep_default_na=False)
d10 = pd.read_csv(DATA / "d10_consent_registry.csv", dtype=str, keep_default_na=False)
roster = pd.read_csv(DATA / "collector_roster.csv", dtype=str)
by_ag = {ag: list(roster[roster.agency_id == ag].collector_id) for ag in AGENCY_NAMES}

CONNECTED = ["CONNECTED_PTP", "CONNECTED_RTP", "CONNECTED_CALLBACK", "CONNECTED_DISPUTE"]
OUTCOME_LINE = {"CONNECTED_PTP": "ptp", "CONNECTED_RTP": "rtp", "CONNECTED_CALLBACK": "cb", "CONNECTED_DISPUTE": "disp"}
LINES_EN = dict(   # English-only for the new slice -- full multi-language flavour already exists in the original
                   # 2,000-account transcripts; duplicating 5 languages here added no new verification signal.
    greet="Good {tod}, this is {agent} calling from {agency} on behalf of your lender. Am I speaking with {name}?",
    yes="Yes, speaking.", ask_dob="For security, could you please confirm your date of birth?",
    dob="It is {dob}.", verified="Thank you, your identity is verified.",
    disclose="I am calling about your {product} account ending {last4}. The total amount due is Rs {amt}.",
    context="I am calling regarding your loan account with us.",
    wrong_dob="It is {wdob}.", mismatch="Sorry, that does not match our records.",
    refuse="Why should I share that with you? I won't.",
    wrongno="No, there is no {name} here. You have the wrong number.",
    sorry="Apologies for the inconvenience. We will update our records. Thank you.",
    tp_disclose="Please inform {first} that Rs {amt} is overdue on his loan and he must pay immediately.",
    ptp="I will pay Rs {part} by next Friday.", rtp="I cannot pay right now, I lost my job.",
    cb="Please call me back in the evening.", disp="I have already paid this, I dispute the amount.",
    close="Thank you for your time. Have a good day.")


# ---------------------------------------------------------------- new borrowers + accounts
print(f"Generating {N_NEW_ACCOUNTS:,} new accounts...")
n_borrowers_new = round(N_NEW_ACCOUNTS * 1800 / 2000)   # same accounts-per-borrower ratio as the original 2,000
borrowers = {}
for i in range(1, n_borrowers_new + 1):
    bid = f"BRW9{i:06d}"
    city, st = rng.choice(CITIES)
    borrowers[bid] = dict(name=f"{rng.choice(FIRST)} {rng.choice(LAST)}", city=city, state=st,
                          dob=rand_date(date(1965, 1, 1), date(2000, 12, 31)), lang=rng.choice(LANG[st]))
bids = list(borrowers)

accounts = []
acc_no = 200001
for lender, n in LENDERS_NEW.items():
    for _ in range(n):
        accounts.append(dict(account_id=f"ACC{acc_no}", lender_id=lender))
        acc_no += 1
rng.shuffle(accounts)
for i, a in enumerate(accounts):
    a["borrower_id"] = bids[i] if i < len(bids) else rng.choice(bids)
accounts.sort(key=lambda a: a["account_id"])

for a in accounts:
    b = borrowers[a["borrower_id"]]
    sanc = rng.choice([50000, 75000, 100000, 150000, 200000, 300000, 500000])
    dpd = rng.choice([rng.randint(1, 29), rng.randint(30, 59), rng.randint(60, 89), rng.randint(90, 180)])
    a.update(product=rng.choice(PRODUCTS), sanction_amount=sanc,
             total_dues=round(sanc * rng.uniform(0.05, 0.6), 0), dpd=dpd,
             bucket="X" if dpd < 30 else "B1" if dpd < 60 else "B2" if dpd < 90 else "NPA",
             disbursal_date=rand_date(date(2022, 1, 1), date(2025, 6, 30)),
             city=b["city"], state=b["state"], branch=f"{b['city']} Main",
             legal_flag="Y" if dpd >= 90 and rng.random() < 0.4 else "N",
             agency_id=rng.choice(LENDER_AGENCIES[a["lender_id"]]))
ACC = {a["account_id"]: a for a in accounts}

# ---------------------------------------------------------------- D4 contact history
print("Generating contact history (this is the big one, ~2.9M rows)...")
contacts = []
cno = 90000001


def new_contact(acc, channel, ts, **kw):
    global cno
    a = ACC[acc]
    row = dict(contact_id=f"CT{cno}", account_id=acc, channel=channel, direction="OUTBOUND",
               number_or_handle=a.setdefault("phone", mask_phone()), contact_ts=ts, template_id="",
               collector_id="", agency_id=a["agency_id"], disposition="", duration_sec=0)
    row.update(kw)
    contacts.append(row)
    cno += 1
    return row


def voice_disposition():
    r = rng.random()
    if r < 0.12:
        return rng.choice(CONNECTED), rng.randint(45, 420)
    if r < 0.13:
        return "WRONG_NUMBER", rng.randint(15, 60)
    return rng.choice(["NO_ANSWER", "NO_ANSWER", "BUSY", "SWITCHED_OFF", "NOT_REACHABLE"]), 0


def voice_call(acc, d, t):
    a = ACC[acc]
    disp, dur = voice_disposition()
    # Any collector for this agency, including whichever happen to be the original dataset's trap
    # collectors (certificate gaps etc.) -- a new account calling a trap collector in their bad window
    # is still a correctly-computed real gap, not a problem; no new traps are hand-injected here.
    return new_contact(acc, "VOICE", datetime.combine(d, t), collector_id=rng.choice(by_ag[a["agency_id"]]),
                       disposition=disp, duration_sec=dur)


tpl = pd.read_csv(DATA / "d11_template_registry.csv", dtype=str)
WA_OK_TPL = list(tpl[(tpl.channel == "WHATSAPP") & (tpl.approval_status == "APPROVED") &
                    (tpl.approved_date.fillna("9999") < "2026-01-01")].template_id)
SMS_TPL = list(tpl[tpl.channel == "SMS"].template_id)

all_days = [P_START + timedelta(days=i) for i in range((P_END - P_START).days + 1)]
for a in accounts:
    acc, lender = a["account_id"], a["lender_id"]
    h1, h2 = hours_window(lender)
    nv = rng.randint(8, 35)
    days = sorted(rng.sample(all_days, nv))
    for d in days:
        k = 2 if rng.random() < 0.08 else 1
        for _ in range(k):
            voice_call(acc, d, rand_time(h1.hour, 5, h2.hour - 1, 55))
    # hours near-misses (lighter trap density than the original -- this slice doesn't need to re-prove
    # the hand-crafted edge cases, those already live in the original 2,000; a touch of natural variance
    # is enough to keep the new slice from looking artificially perfect)
    if rng.random() < 0.03:
        d = rng.choice(all_days)
        t = rng.choice([rand_time(7, 0, 7, 50), rand_time(19, 10, 21, 30)])
        voice_call(acc, d, t)
    if rng.random() < 0.02:
        d = rng.choice(all_days)
        for _ in range(rng.randint(4, 5)):
            voice_call(acc, d, rand_time(h1.hour, 5, h2.hour - 1, 55))
    if rng.random() < 0.9:
        for d in sorted(rng.sample(all_days, rng.randint(2, 8))):
            new_contact(acc, "WHATSAPP", datetime.combine(d, rand_time(9, 0, 20, 0)),
                       template_id=rng.choice(WA_OK_TPL), disposition=rng.choice(["SENT", "DELIVERED", "READ", "READ"]))
    for d in rng.sample(all_days, rng.randint(0, 4)):
        new_contact(acc, "SMS", datetime.combine(d, rand_time(9, 0, 20, 0)),
                   template_id=rng.choice(SMS_TPL), disposition="DELIVERED")

print(f"  {len(contacts):,} new contact rows generated.")

# ---------------------------------------------------------------- D5 transcripts -- ~100% of connected
# calls get one (same coverage ratio as the original generator; see module docstring for why this
# deliberately doesn't match the spec's literal "audio for 2,000 of 60,000" line).
print("Generating call transcripts...")
transcripts = []
connected_calls = [c for c in contacts if c["channel"] == "VOICE" and c["disposition"] in CONNECTED + ["WRONG_NUMBER"]]
for c in connected_calls:
    if rng.random() < 0.025:   # same "missing transcript" rate as the original -- real-world recording gaps happen
        continue
    a = ACC[c["account_id"]]
    b = borrowers[a["borrower_id"]]
    L = LINES_EN
    hr = c["contact_ts"].hour
    f = dict(tod="morning" if hr < 12 else "afternoon" if hr < 17 else "evening",
             agent=rng.choice(FIRST), agency=AGENCY_NAMES[c["agency_id"]], name=b["name"], first=b["name"].split()[0],
             dob=b["dob"].strftime("%d %B %Y"), wdob=(b["dob"] + timedelta(days=rng.randint(40, 900))).strftime("%d %B %Y"),
             product=a["product"], last4=a["account_id"][-4:], amt=f"{int(a['total_dues']):,}",
             part=f"{int(a['total_dues'] * 0.3):,}")
    if c["disposition"] == "WRONG_NUMBER":
        turns = [("AGENT", "greet"), ("CUSTOMER", "wrongno"), ("AGENT", "sorry")]
    else:
        r = rng.random()
        end = [("CUSTOMER", OUTCOME_LINE[c["disposition"]]), ("AGENT", "close")]
        if r < 0.15:
            turns = [("AGENT", "greet"), ("CUSTOMER", "yes"), ("AGENT", "context"), ("AGENT", "ask_dob"),
                     ("CUSTOMER", "dob"), ("AGENT", "verified"), ("AGENT", "disclose")] + end
        else:
            turns = [("AGENT", "greet"), ("CUSTOMER", "yes"), ("AGENT", "ask_dob"), ("CUSTOMER", "dob"),
                     ("AGENT", "verified"), ("AGENT", "disclose")] + end
    ts = c["contact_ts"] - IST + timedelta(seconds=rng.randint(2, 6))
    for i, (spk, key) in enumerate(turns, 1):
        transcripts.append(dict(call_id=c["contact_id"], account_id=c["account_id"], turn_no=i, speaker=spk,
                                text=L[key].format(**f), language="en",
                                asr_confidence=round(rng.uniform(0.55, 0.99) if rng.random() < 0.1 else rng.uniform(0.82, 0.99), 2),
                                turn_start_ts=ts.replace(microsecond=0)))
        ts += timedelta(seconds=rng.randint(4, 25))
print(f"  {len(transcripts):,} new transcript turns generated.")

# ---------------------------------------------------------------- D10 consent registry (UTC)
print("Generating consent registry...")
consents = []
cons_no = 9000001


def add_consent(bid, ch, cap_utc, opt_utc=None, src=None):
    global cons_no
    consents.append(dict(consent_id=f"CNS{cons_no:07d}", borrower_id=bid, channel=ch,
                         consent_status="OPTED_OUT" if opt_utc else "ACTIVE",
                         captured_at=cap_utc.replace(microsecond=0),
                         opt_out_at=opt_utc.replace(microsecond=0) if opt_utc else None,
                         consent_source=src or rng.choice(["APP_ONBOARDING", "LOAN_AGREEMENT_ESIGN", "WEB_FORM", "BRANCH_FORM"])))
    cons_no += 1


wa_by_brw = {}
for c in contacts:
    if c["channel"] == "WHATSAPP":
        wa_by_brw.setdefault(ACC[c["account_id"]]["borrower_id"], []).append(c)
for bid in bids:
    base = datetime.combine(rand_date(date(2022, 1, 1), date(2025, 9, 30)), rand_time(3, 0, 14, 0))
    add_consent(bid, "SMS", base)
    if rng.random() < 0.8:
        add_consent(bid, "VOICE", base)
    msgs = sorted(wa_by_brw.get(bid, []), key=lambda c: c["contact_ts"])
    if not msgs:
        if rng.random() < 0.5:
            add_consent(bid, "WHATSAPP", base)
        continue
    utc = [m["contact_ts"] - IST for m in msgs]
    r = rng.random()
    if r < 0.03:
        pass   # no consent record at all -- a real, naturally occurring gap, not a hand-injected trap
    elif r < 0.08:
        add_consent(bid, "WHATSAPP", utc[0] + timedelta(minutes=rng.randint(3, 300)), src="WHATSAPP_OPTIN")
    elif r < 0.11 and len(msgs) >= 3:
        k = rng.randint(1, len(msgs) - 1)
        opt = utc[k - 1] + (utc[k] - utc[k - 1]) * rng.uniform(0.2, 0.8)
        add_consent(bid, "WHATSAPP", base, opt_utc=opt)
    else:
        add_consent(bid, "WHATSAPP", base)
for c in consents:
    c["dnd_status"] = "N"
for bid in rng.sample(bids, round(len(bids) * 0.1)):
    for c in consents:
        if c["borrower_id"] == bid:
            c["dnd_status"] = "Y"
print(f"  {len(consents):,} new consent rows generated.")

# ---------------------------------------------------------------- write (original rows preserved, new
# rows appended -- never the reverse). Existing 13-column accounts schema only, no new columns.
print("Writing CSVs...")
new_d1 = pd.DataFrame(accounts)[["account_id", "lender_id", "borrower_id", "product", "sanction_amount",
                                "total_dues", "dpd", "bucket", "disbursal_date", "city", "state", "branch",
                                "legal_flag"]]
pd.concat([d1, new_d1], ignore_index=True).to_csv(DATA / "d1_account_master_thin.csv", index=False)

new_d4 = pd.DataFrame(contacts)
new_d4["contact_ts"] = pd.to_datetime(new_d4["contact_ts"]).dt.strftime("%Y-%m-%d %H:%M:%S")
new_d4 = new_d4[["contact_id", "account_id", "channel", "direction", "number_or_handle", "contact_ts",
                "template_id", "collector_id", "agency_id", "disposition", "duration_sec"]]
pd.concat([d4, new_d4], ignore_index=True).sample(frac=1, random_state=44).to_csv(DATA / "d4_contact_history.csv", index=False)

new_d5 = pd.DataFrame(transcripts)
new_d5["turn_start_ts"] = pd.to_datetime(new_d5["turn_start_ts"]).dt.strftime("%Y-%m-%d %H:%M:%S")
pd.concat([d5, new_d5], ignore_index=True).to_csv(DATA / "d5_call_transcripts.csv", index=False)

new_d10 = pd.DataFrame(consents)
for col in ["captured_at", "opt_out_at"]:
    new_d10[col] = pd.to_datetime(new_d10[col]).dt.strftime("%Y-%m-%d %H:%M:%S")
pd.concat([d10, new_d10], ignore_index=True).to_csv(DATA / "d10_consent_registry.csv", index=False)

print(f"Done. Totals: accounts={len(d1) + len(new_d1):,}, contacts={len(d4) + len(new_d4):,}, "
     f"transcript turns={len(d5) + len(new_d5):,}, consent rows={len(d10) + len(new_d10):,}.")
