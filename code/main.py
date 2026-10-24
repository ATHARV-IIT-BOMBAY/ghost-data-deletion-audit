"""Ghost Data Still Exists — deletion-audit agent. Entry point.

    python3 code/main.py                                   # all 518 requests → ./output.csv
    python3 code/main.py --requests dataset/sample_deletion_requests.csv --out /tmp/sample_pred.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "code"))

from ghostaudit.engine import CaseResult, Engine  # noqa: E402
from ghostaudit.schema import dumps_steps, validate_row  # noqa: E402


def run(requests: Path, out: Path, trace_path: Path | None) -> None:
    engine = Engine(ROOT / "dataset")
    for p in engine.ingest_problems:
        print(f"INGEST PROBLEM: {p}", file=sys.stderr)
    with open(requests, newline="", encoding="utf-8") as f:
        reqs = sorted(csv.DictReader(f), key=lambda r: r["case_id"])
    # Workflow events from every known request compete for the same accounts, so assignment is
    # global over the evaluation and sample requests together, whichever file is being run.
    every: dict[str, dict] = {}
    for name in ("deletion_requests.csv", "sample_deletion_requests.csv"):
        with open(ROOT / "dataset" / name, newline="", encoding="utf-8") as f:
            every.update({r["case_id"]: r for r in csv.DictReader(f)})
    engine.assign_workflow_accounts(list(every.values()))
    print(f"workflow links over {engine.workflow_stats[0]} deletion cases: {engine.workflow_stats[1]}")

    results: list[CaseResult] = []
    for r in reqs:
        try:
            res = engine.audit(r)
        except Exception as e:  # one bad case must not sink the run; it is visible in the trace
            res = CaseResult(r["case_id"], "INCOMPLETE", [], [f"ERROR {type(e).__name__}: {e}"])
        errs = validate_row(res.status, res.steps)
        if errs:
            res.trace.append("CONTRACT: " + "; ".join(errs))
        results.append(res)

    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["case_id", "deletion_status", "steps_json"])
        for res in results:
            w.writerow([res.case_id, res.status, dumps_steps(res.steps)])
    if trace_path:
        with open(trace_path, "w", encoding="utf-8") as f:
            for res in results:
                f.write(json.dumps({"case_id": res.case_id, "status": res.status, "trace": res.trace}) + "\n")
    counts: dict[str, int] = {}
    for res in results:
        counts[res.status] = counts.get(res.status, 0) + 1
    bad = sum(any(t.startswith(("CONTRACT", "ERROR")) for t in r.trace) for r in results)
    print(f"wrote {len(results)} rows to {out}: {counts}; rows with contract/errors: {bad}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=Path, default=ROOT / "dataset" / "deletion_requests.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "output.csv")
    ap.add_argument("--trace", type=Path, help="write a per-case JSONL decision trace")
    args = ap.parse_args()
    run(args.requests, args.out, args.trace)


if __name__ == "__main__":
    main()
