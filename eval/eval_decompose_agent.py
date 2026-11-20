"""
One-off: measure decompose_agent.decompose_with_tools() against the same
REQ-01/02/03 ground truth eval/score.py uses -- but WITHOUT touching
out/REQ-*/ (the proven one-shot/hand_checks baseline). Writes to a separate
out dir and scores against that instead.

    python -m eval.eval_decompose_agent

Not wired into eval/score.py's normal --run path on purpose: this is a
one-time comparison, not something every CI run needs to pay Sarvam for.
"""
import json
import sys
from pathlib import Path

import pandas as pd

from agent.config import DATA_DIR, HAND_CHECKS, ROOT
from agent.decompose_agent import decompose_with_tools
from agent.file_text import extract_text
from agent.run_checks import run
from agent.tools import Tools

AGENT_OUT = ROOT / "out_agent_eval"
AGENT_CHECKS = ROOT / "eval" / "agent_checks"


def build_checks():
    AGENT_CHECKS.mkdir(parents=True, exist_ok=True)
    for hc_path in sorted(HAND_CHECKS.glob("REQ-*.json")):
        hc = json.load(open(hc_path))
        rid = hc["request_id"]
        src = DATA_DIR / hc["source"]
        text = extract_text(src.name, src.read_bytes())
        print(f"--- {rid}: decompose_with_tools() on {src.name} ({len(text)} chars) ---")
        result = decompose_with_tools(
            request_text=text, request_id=rid, lender_id=hc["lender_id"],
            period=hc["period"], sample_file=hc["sample_file"], source=hc["source"],
        )
        if "needs_clarification" in result:
            print(f"  !! asked for clarification instead of checks.json: {result['needs_clarification']}")
            print(f"  trace: {result['agent_trace']}")
            continue
        print(f"  {len(result['checks'])} checks, {len(result['unsupported'])} unsupported, "
              f"{len(result.get('agent_trace', []))} tool call(s)")
        for t in result.get("agent_trace", []):
            print(f"    agent called {t['tool']}({t.get('args', {})})")
        for c in result["checks"]:
            print(f"    {c['check_id']} {c['check_type']!r}  source_text={c['source_text']!r}")
        out_path = AGENT_CHECKS / f"{rid}.json"
        out_path.write_text(json.dumps(result, indent=2))


def run_checks_and_score():
    tools = Tools()
    for p in sorted(AGENT_CHECKS.glob("REQ-*.json")):
        run(p, tools, out_dir=AGENT_OUT)

    # same metrics as eval/score.py's score(), pointed at AGENT_OUT instead of OUT_DIR
    truth = pd.read_csv(DATA_DIR / "ground_truth" / "ground_truth_account_check.csv", dtype=str)
    preds = [pd.read_csv(p, dtype=str) for p in sorted(AGENT_OUT.glob("REQ-*/verdicts.csv"))]
    if not preds:
        sys.exit("No outputs in out_agent_eval/ -- build_checks() must have produced 0 usable checks.json files")
    pred = pd.concat(preds)
    KEY = ["request_id", "account_id", "check_id"]
    m = truth.merge(pred, on=KEY, how="left", suffixes=("_true", "_pred"))
    m = m[m["request_id"].isin(pred["request_id"].unique())]
    m["status_pred"] = m["status_pred"].fillna("MISSING")
    m["evidence_count"] = pd.to_numeric(m["evidence_count"]).fillna(0)

    tg, pg = m.status_true == "GAP", m.status_pred == "GAP"
    tp, fn, fp = (tg & pg).sum(), (tg & ~pg).sum(), (~tg & pg).sum()
    unsupported = ((m.status_pred == "EVIDENCED") & ((m.evidence_count == 0) | tg)).sum()
    sev_ok = (m[tg & pg].severity_true == m[tg & pg].severity_pred).sum()
    recall = tp / tg.sum() if tg.sum() else 1.0
    false_gap_rate = fp / pg.sum() if pg.sum() else 0.0
    status_acc = (m.status_true == m.status_pred).mean()

    print("\n=== decompose_agent.py vs ground truth (checks it extracted itself, not hand_checks) ===")
    print(f"  requests scored    : {sorted(pred['request_id'].unique())}  "
          f"({sorted(set(truth.request_id) - set(pred.request_id))} missing -- needs_clarification or 0 checks)")
    print(f"  true gaps in scope : {int(tg.sum())}")
    print(f"  gap recall         : {recall:.1%}   (one-shot baseline: 100.0%)")
    print(f"  false gap rate     : {false_gap_rate:.1%}   (one-shot baseline: 1.7%)")
    print(f"  unsupported claims : {int(unsupported)}       (one-shot baseline: 0)")
    print(f"  severity accuracy  : {sev_ok / tp if tp else 1.0:.1%}   (one-shot baseline: 100.0%)")
    print(f"  status accuracy    : {status_acc:.1%}   (one-shot baseline: 99.9%)")
    mism = m[m.status_true != m.status_pred]
    if len(mism):
        print(f"\n  {len(mism)} status mismatches:")
        print(mism[KEY + ["check_type_true", "status_true", "status_pred"]].to_string(index=False))


def eval_ambiguous_severity_case():
    """The scenario missing from REQ-01/02/03 (all three state their own
    grading scale) and from the earlier comparison run: a short pasted
    request that names checks but states NO severity section at all --
    realistic per STATUS.md Step 10's "minimal one-sentence pasted request"
    bug. No ground truth exists for this ad hoc text, so this can't score
    gap recall -- what it proves is narrower but real: decompose_agent runs
    this end to end, against real account data, without crashing, and the
    output is gradable for every check_type it extracted."""
    from agent.config import HAND_CHECKS
    from agent.decompose import GAP_TYPES_BY_CHECK
    from agent.severity import grade
    from agent.verdict import Finding

    hc = json.load(open(HAND_CHECKS / "REQ-01.json"))  # reuse L001's real sample/accounts
    text = ("Please confirm that collection calls respected the permitted calling hours and "
            "call frequency limits, and that WhatsApp reminders were sent only to borrowers "
            "who had consented to WhatsApp communication.")
    print(f"\n--- ambiguous-severity case: decompose_with_tools() on a {len(text)}-char request "
          f"with NO severity section ---")
    result = decompose_with_tools(
        request_text=text, request_id="AMBIG-SEV-01", lender_id=hc["lender_id"],
        period=hc["period"], sample_file=hc["sample_file"], source="synthetic (no ground truth)",
    )
    if "needs_clarification" in result:
        print(f"  !! asked for clarification: {result['needs_clarification']}")
        return
    print(f"  {len(result['checks'])} checks extracted, {len(result.get('agent_trace', []))} tool call(s), "
          f"severity_order={result['severity_order']}")
    for c in result["checks"]:
        rule_severities = [r["severity"] for r in result["severity_rules"] if r.get("check_type") == c["check_type"]]
        print(f"    {c['check_id']} {c['check_type']!r} -> severity_rules has {rule_severities}")
    # the actual guarantee that matters: run_checks.py's severity.grade() must not raise
    # "No severity rule matches" for any check_type the model extracted -- same assertion
    # tests/test_decompose.py makes on synthetic input, here against the live LLM's real output.
    # Use a real gap_type per check_type (what checks.py would actually emit), not a placeholder,
    # so a model-written gap_types-qualified rule is exercised honestly instead of skipped.
    for c in result["checks"]:
        real_gap_type = GAP_TYPES_BY_CHECK[c["check_type"]][0]
        sev = grade(Finding(real_gap_type, ["REC1"], "detail"), c["check_type"], result["severity_rules"])
        print(f"    {c['check_type']}/{real_gap_type} grades to: {sev}  (no crash)")


if __name__ == "__main__":
    build_checks()
    run_checks_and_score()
    eval_ambiguous_severity_case()
