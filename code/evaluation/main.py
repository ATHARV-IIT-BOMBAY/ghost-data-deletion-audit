"""Scorer for Ghost Data Still Exists — written before the solver (DECISIONS.md D3).

Scores a predictions CSV against labelled answers on every axis the README says the
grader considers: deletion status, discovered records/risks (step P/R/F1), cited
evidence and policy decisions (field accuracy), and step completeness + order
(sequence accuracy). Also validates the output contract row by row.

Usage (from the repository root):
    python3 code/evaluation/main.py                       # score ./output.csv on all 32 samples
    python3 code/evaluation/main.py --pred X.csv --split dev
    python3 code/evaluation/main.py --baseline reported   # score a no-model baseline
    python3 code/evaluation/main.py --gold path/to/custom_gold.csv --pred path/to/pred.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "code"))

from ghostaudit.schema import OPERATIONS, STEP_FIELDS, validate_row  # noqa: E402

DEFAULT_GOLD = ROOT / "dataset" / "sample_output.csv"
SAMPLE_REQUESTS = ROOT / "dataset" / "sample_deletion_requests.csv"
HEADER = ["case_id", "deletion_status", "steps_json"]
# Fixed holdout (DECISIONS.md D4): every 4th sample case, never used for tuning.
HOLDOUT_EVERY = 4

Row = tuple[str, list]  # (status, steps)


def read_rows(path: Path) -> dict[str, Row]:
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != HEADER:
            raise SystemExit(f"{path}: header must be {','.join(HEADER)}, got {reader.fieldnames}")
        rows: dict[str, Row] = {}
        for r in reader:
            try:
                steps = json.loads(r["steps_json"])
            except json.JSONDecodeError:
                steps = None  # counted as a contract violation, scored as empty
            rows[r["case_id"]] = (r["deletion_status"], steps)
        return rows


def in_split(case_id: str, split: str) -> bool:
    if split == "all":
        return True
    holdout = int(case_id.rsplit("_", 1)[1]) % HOLDOUT_EVERY == 0
    return holdout if split == "holdout" else not holdout


def baseline(kind: str) -> dict[str, Row]:
    """No-model reference points the solver has to beat."""
    with open(SAMPLE_REQUESTS, newline="", encoding="utf-8") as f:
        reqs = list(csv.DictReader(f))
    if kind == "reported":  # trust the workflow's own claim
        return {r["case_id"]: ("COMPLETE" if r["reported_status"] == "COMPLETE" else "INCOMPLETE", [])
                for r in reqs}
    return {r["case_id"]: ("INCOMPLETE", []) for r in reqs}  # "incomplete": majority class


def core(s: dict) -> tuple:
    """Step identity without evidence: what was found/planned for which resource."""
    return (s.get("phase"), s.get("system_id"), s.get("resource_id"), s.get("operation"), s.get("state"))


def full(s: dict) -> tuple:
    return core(s) + (s.get("evidence_id"),)


def prf(tp: int, n_pred: int, n_gold: int) -> tuple[float, float, float]:
    p = tp / n_pred if n_pred else 1.0
    r = tp / n_gold if n_gold else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


def lcs(a: list, b: list) -> int:
    dp = [0] * (len(b) + 1)
    for x in a:
        prev = 0
        for j, y in enumerate(b, start=1):
            prev, dp[j] = dp[j], (prev + 1 if x == y else max(dp[j], dp[j - 1]))
    return dp[-1]


def score(pred: dict[str, Row], gold: dict[str, Row]) -> dict:
    m: Counter = Counter()
    per_op: dict[str, Counter] = {op: Counter() for op in OPERATIONS}
    field_hits: Counter = Counter()
    field_total = 0
    order_sum = 0.0
    misses: list[str] = []
    contract_errors: dict[str, list[str]] = {}

    for cid, (g_status, g_steps) in gold.items():
        p_status, p_steps = pred.get(cid, ("<missing>", []))
        errs = ["row missing"] if cid not in pred else validate_row(p_status, p_steps)
        if errs:
            contract_errors[cid] = errs
        p_steps = p_steps if isinstance(p_steps, list) else []
        p_steps = [s for s in p_steps if isinstance(s, dict)]

        m["cases"] += 1
        m["status_ok"] += p_status == g_status
        exact = p_status == g_status and [full(s) for s in p_steps] == [full(s) for s in g_steps]
        m["row_exact"] += exact

        for key, name in ((core, "core"), (full, "full")):
            pc, gc = Counter(map(key, p_steps)), Counter(map(key, g_steps))
            m[f"{name}_tp"] += sum((pc & gc).values())
            m[f"{name}_pred"] += sum(pc.values())
            m[f"{name}_gold"] += sum(gc.values())

        for op in OPERATIONS:
            pc = Counter(core(s) for s in p_steps if s.get("operation") == op)
            gc = Counter(core(s) for s in g_steps if s.get("operation") == op)
            per_op[op].update(tp=sum((pc & gc).values()), pred=sum(pc.values()), gold=sum(gc.values()))

        # Field accuracy, position-aligned: step i of the prediction vs step i of the gold.
        for i in range(max(len(p_steps), len(g_steps))):
            field_total += 1
            ps = p_steps[i] if i < len(p_steps) else {}
            gs = g_steps[i] if i < len(g_steps) else {}
            for f in STEP_FIELDS:
                field_hits[f] += bool(gs) and ps.get(f) == gs.get(f)

        gk, pk = [core(s) for s in g_steps], [core(s) for s in p_steps]
        m["seq_exact"] += gk == pk
        order_sum += lcs(pk, gk) / max(len(pk), len(gk)) if (pk or gk) else 1.0

        if not exact:
            pc, gc = Counter(map(full, p_steps)), Counter(map(full, g_steps))
            missing, extra = list((gc - pc).elements()), list((pc - gc).elements())
            misses.append(f"{cid}: status {p_status} vs gold {g_status}; "
                          f"missing {len(missing)}, extra {len(extra)}"
                          + "".join(f"\n      - {x}" for x in missing[:6])
                          + "".join(f"\n      + {x}" for x in extra[:6]))

    n = m["cases"] or 1
    return {
        "cases": m["cases"],
        "status_accuracy": m["status_ok"] / n,
        "row_exact": m["row_exact"] / n,
        "step_core_prf": prf(m["core_tp"], m["core_pred"], m["core_gold"]),
        "step_full_prf": prf(m["full_tp"], m["full_pred"], m["full_gold"]),
        "field_accuracy": {f: field_hits[f] / field_total if field_total else 1.0 for f in STEP_FIELDS},
        "sequence_exact": m["seq_exact"] / n,
        "sequence_order_lcs": order_sum / n,
        "per_operation": {op: prf(c["tp"], c["pred"], c["gold"]) + (c["gold"],)
                          for op, c in per_op.items() if c["gold"] or c["pred"]},
        "contract_errors": contract_errors,
        "misses": misses,
    }


def file_checks(path: Path, expected_ids: list[str] | None) -> list[str]:
    """Whole-file contract checks for a submission-sized output.csv."""
    with open(path, newline="", encoding="utf-8") as f:
        ids = [r["case_id"] for r in csv.DictReader(f)]
    problems = []
    if len(ids) != len(set(ids)):
        problems.append("duplicate case_id rows")
    if ids != sorted(ids):
        problems.append("rows not sorted by case_id")
    if expected_ids is not None and set(ids) != set(expected_ids):
        problems.append(f"case_id set differs from deletion_requests.csv "
                        f"(missing {len(set(expected_ids) - set(ids))}, extra {len(set(ids) - set(expected_ids))})")
    return problems


def report(res: dict, verbose: bool) -> str:
    p, r, f = res["step_core_prf"]
    fp, fr, ff = res["step_full_prf"]
    lines = [
        f"cases scored            {res['cases']}",
        f"status accuracy         {res['status_accuracy']:.3f}",
        f"row exact match         {res['row_exact']:.3f}",
        f"steps (no evidence)     P {p:.3f}  R {r:.3f}  F1 {f:.3f}",
        f"steps (with evidence)   P {fp:.3f}  R {fr:.3f}  F1 {ff:.3f}",
        f"sequence exact          {res['sequence_exact']:.3f}",
        f"sequence order (LCS)    {res['sequence_order_lcs']:.3f}",
        "field accuracy (position-aligned):",
        *(f"    {k:12} {v:.3f}" for k, v in res["field_accuracy"].items()),
        "per operation (P / R / F1 / gold count):",
        *(f"    {op:26} {a:.2f} {b:.2f} {c:.2f} {n:3d}" for op, (a, b, c, n) in res["per_operation"].items()),
        f"contract violations     {len(res['contract_errors'])} rows",
    ]
    if verbose:
        lines += [f"  {cid}: {'; '.join(e[:3])}" for cid, e in list(res["contract_errors"].items())[:10]]
        lines += ["misses:"] + [f"  {x}" for x in res["misses"]]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pred", type=Path, default=ROOT / "output.csv")
    ap.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    ap.add_argument("--split", choices=("all", "dev", "holdout"), default="all")
    ap.add_argument("--baseline", choices=("reported", "incomplete"))
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    gold = {k: v for k, v in read_rows(args.gold).items() if in_split(k, args.split)}
    pred = baseline(args.baseline) if args.baseline else read_rows(args.pred)
    print(f"gold={args.gold.name} split={args.split} pred={args.baseline or args.pred}")
    print(report(score(pred, gold), args.verbose))

    if not args.baseline and args.gold == DEFAULT_GOLD and args.pred.exists():
        with open(ROOT / "dataset" / "deletion_requests.csv", newline="", encoding="utf-8") as f:
            expected = [r["case_id"] for r in csv.DictReader(f)]
        if len(read_rows(args.pred)) > len(gold):  # a full submission file, not a sample-only run
            for problem in file_checks(args.pred, expected):
                print(f"FILE CHECK: {problem}")


if __name__ == "__main__":
    main()
