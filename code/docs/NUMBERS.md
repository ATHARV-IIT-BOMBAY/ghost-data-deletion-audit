# NUMBERS.md — every figure and the command that regenerates it

Run from the repository root. Prefix with `env -u GEMINI_API_KEY` to guarantee zero API calls (the caches serve every model answer).
Setup for the sample runs: `S=/tmp/ga; mkdir -p $S`.

| Figure | Value | Command |
|---|---|---|
| Sample predictions | — | `python3 code/main.py --requests dataset/sample_deletion_requests.csv --out $S/s.csv --trace $S/s.jsonl` |
| Status accuracy (32) | 1.000 | `python3 code/evaluation/main.py --pred $S/s.csv` |
| Step P / R / F1, with evidence | 0.995 / 0.938 / 0.965 | same |
| Row exact / sequence exact | 0.625 / 0.625 | same |
| Sequence order (LCS) | 0.915 | same |
| Field accuracy (step_no, phase, system, resource, operation, state, evidence) | 0.933, 0.933, 0.820, 0.820, 0.835, 0.835, 0.928 | same |
| Per-operation F1 (findings + remediation) | 1.00 for all 9 | same |
| VERIFY_RECORD_ABSENT / PRESENT / REDACTED / ANONYMIZED recall | 0.92 / 0.92 / 1.00 / 0.50 | same |
| Dev F1 (24) | 0.969 | `... --split dev` |
| Holdout F1 (8) | 0.952 | `... --split holdout` |
| Baseline: trust reported_status | status 0.531, step F1 0 | `python3 code/evaluation/main.py --baseline reported` |
| Baseline: always INCOMPLETE | status 0.688, step F1 0 | `python3 code/evaluation/main.py --baseline incomplete` |
| No-model ablation (samples) | status 1.000, F1 0.965 | `python3 code/main.py --no-models --requests dataset/sample_deletion_requests.csv --out $S/nm.csv` then score |
| Custom cases | 6/6 | `python3 code/evaluation/custom_cases.py` |
| Unit tests | 25 pass, 0.40 s | `python3 -m unittest discover -s code/tests -t code` |
| Workflow links | account 429/540, analytics 99/540 | printed by any `code/main.py` run |
| Full run statuses | 353 INCOMPLETE / 155 COMPLETE / 10 NOT_APPLICABLE, 0 contract errors | `python3 code/main.py --out $S/full.csv` (then `git checkout -- code/evaluation/last_run_usage.json`) |
| Full run identical to submitted file | yes | `cmp $S/full.csv output.csv` |
| Evidence cited after audit_at | 0 | check script in `research/08_improvement_plan.md` §T8 / `code/tests/test_engine.py` |
| Final cold run usage | 29 calls; 40,878 in / 21,867 out; 270 s | `code/evaluation/last_run_usage.json` (written by the cold run; see `usage_report.md`) |
| Usage by purpose | intent 16 calls (21,220 / 19,833); vision 11 (16,614 / 1,889); escalation 2 (3,044 / 145) | same file, `per_purpose` |
| Cost estimate | $0.0715 total, $0.000138 per request (list price); $0 billed | `usage_report.md` (prices from aggregators citing Google's page) |
| Status-filter evidence | COMPLETE↔SUCCESS 20/20, NOT_STARTED↔FAILED 12/12, PARTIALLY_COMPLETE↔PARTIAL\|SUCCESS 5/5 | D24 in `DECISIONS.md` (sample gold accounts joined to their deletion-worker events) |
| Gold findings citing post-request evidence | 41/41 | D19 |
