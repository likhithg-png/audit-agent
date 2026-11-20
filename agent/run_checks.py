"""
Run the deterministic pipeline (Stages 3-5) for one request, from a checks.json.

    python -m agent.run_checks REQ-01            # uses eval/hand_checks/REQ-01.json
    python -m agent.run_checks --all
    python -m agent.run_checks REQ-01 --checks path/to/llm_checks.json

Outputs out/<REQ>/verdicts.csv, gap_items.csv, summary.csv -- AND, additively,
persists the same run to Postgres (audit.runs / audit.verdicts / audit.gap_items)
with its own run_id, so every run is kept as queryable history, not just
whatever the last CLI invocation happened to leave in out/. The CSVs are
unchanged -- pack.py, letter.py, eval/score.py and app.py keep reading them
exactly as before.
"""
import argparse
import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
from psycopg.types.json import Json

from . import db
from .checks import CHECKS, Context
from .config import DATA_DIR, HAND_CHECKS, OUT_DIR
from .rules import Policy
from .severity import grade, worst
from .tools import Tools


def _persist_run(spec, vdf, gaps_rows):
    """Insert one audit.runs row + its verdicts/gap_items. Never raises into
    the caller -- a DB hiccup here must not stop the CSVs (the proven output
    path) from being written."""
    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO audit.runs
                   (request_id, lender_id, period_from, period_to, sample_file, source,
                    checks, unsupported, severity_order, severity_rules)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING run_id""",
                (spec["request_id"], spec.get("lender_id"), spec["period"]["from"], spec["period"]["to"],
                 spec.get("sample_file"), spec.get("source"), Json(spec["checks"]),
                 Json(spec.get("unsupported", [])), Json(spec["severity_order"]), Json(spec["severity_rules"])),
            )
            run_id = cur.fetchone()["run_id"]

            if len(vdf):
                cur.executemany(
                    """INSERT INTO audit.verdicts
                       (run_id, request_id, account_id, check_id, check_type, status, severity,
                        items_tested, gap_items, gap_types, cannot_determine_reasons,
                        evidence_count, evidence_ids, rule_refs)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(run_id, r.request_id, r.account_id, r.check_id, r.check_type, r.status,
                      r.severity or None, int(r.items_tested), int(r.gap_items), r.gap_types,
                      r.cannot_determine_reasons, int(r.evidence_count), r.evidence_ids, r.rule_refs)
                     for r in vdf.itertuples()],
                )
            if gaps_rows:
                cur.executemany(
                    """INSERT INTO audit.gap_items
                       (gap_item_id, run_id, request_id, account_id, check_id, check_type,
                        gap_type, severity, record_ids, detail)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(g["gap_item_id"], run_id, g["request_id"], g["account_id"], g["check_id"],
                      g["check_type"], g["gap_type"], g["severity"], g["record_ids"], g["detail"])
                     for g in gaps_rows],
                )
            conn.commit()
        return run_id
    except Exception as e:
        print(f"  warning: could not persist run to Postgres ({e}); CSV output is unaffected")
        return None


def run(spec_path, tools=None, out_dir=OUT_DIR, verbose=True, should_stop=None):
    spec = json.load(open(spec_path))
    rid = spec["request_id"]
    t0 = time.time()
    tools = tools or Tools()
    policy = Policy(tools)
    sample = pd.read_csv(DATA_DIR / spec["sample_file"], dtype=str)["account_id"].tolist()
    unknown = set(sample) - set(tools.accounts().index)
    if unknown:
        raise ValueError(f"{len(unknown)} sampled accounts not found in D1: {sorted(unknown)[:5]}")
    ctx = Context(tools, policy, rid, date.fromisoformat(spec["period"]["from"]), date.fromisoformat(spec["period"]["to"]))
    order = spec["severity_order"]
    verdicts = []
    for chk in spec["checks"]:
        if should_stop and should_stop():
            raise RuntimeError("stopped")  # the caller knows it asked for this
        fn = CHECKS[chk["check_type"]]
        for v in fn(ctx, chk, sample):
            for g in v.gaps:
                g.severity = grade(g, v.check_type, spec["severity_rules"])
            v.severity = worst([g.severity for g in v.gaps], order)
            verdicts.append(v)

    od = Path(out_dir) / rid
    od.mkdir(parents=True, exist_ok=True)
    vdf = pd.DataFrame([v.row() for v in verdicts])
    vdf.to_csv(od / "verdicts.csv", index=False)
    gaps = [dict(gap_item_id=f"{rid}-G{i + 1:04d}", request_id=rid, account_id=v.account_id, check_id=v.check_id,
                 check_type=v.check_type, gap_type=g.gap_type, severity=g.severity,
                 record_ids=";".join(g.record_ids), detail=g.detail)
            for i, (v, g) in enumerate((v, g) for v in verdicts for g in v.gaps)]
    pd.DataFrame(gaps, columns=["gap_item_id", "request_id", "account_id", "check_id", "check_type", "gap_type",
                                "severity", "record_ids", "detail"]).to_csv(od / "gap_items.csv", index=False)
    # The records a check could not test (and why) -- the UI explains "cannot determine" from this.
    und = [dict(request_id=rid, account_id=v.account_id, check_id=v.check_id, check_type=v.check_type,
                reason=u.gap_type, record_id=";".join(u.record_ids), detail=u.detail)
           for v in verdicts for u in v.undetermined]
    pd.DataFrame(und, columns=["request_id", "account_id", "check_id", "check_type", "reason", "record_id",
                               "detail"]).to_csv(od / "undetermined_items.csv", index=False)
    summ = (vdf.pivot_table(index=["check_id", "check_type"], columns="status", values="account_id",
                            aggfunc="count", fill_value=0).reset_index())
    summ.to_csv(od / "summary.csv", index=False)

    run_id = _persist_run(spec, vdf, gaps)  # additive -- CSVs above are already written either way

    if verbose:
        print(f"\n=== {rid}: {len(sample)} accounts x {len(spec['checks'])} checks in {time.time() - t0:.1f}s ===")
        print(summ.to_string(index=False))
        for n in dict.fromkeys(tools.notes):
            print("  note:", n)
        if run_id:
            print(f"  persisted to Postgres: audit.runs.run_id = {run_id}")
    return vdf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("request", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--checks", help="path to a checks.json (default: eval/hand_checks/<REQ>.json)")
    a = ap.parse_args()
    reqs = sorted(p.stem for p in HAND_CHECKS.glob("*.json")) if a.all else [a.request]
    tools = Tools()
    for r in reqs:
        run(a.checks or HAND_CHECKS / f"{r}.json", tools)


if __name__ == "__main__":
    main()
