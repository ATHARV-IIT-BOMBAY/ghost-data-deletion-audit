# NUMBERS.md — every figure and the command that regenerates it

Run from the repository root. The shipped caches (`code/cache/`) serve every model answer, so these commands make zero API calls. To *guarantee* it, also move `.env` out of the repo root: the key is read from there as well as from the shell.
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
| Unit tests | 31 pass, 0.42 s | `python3 -m unittest discover -s code/tests -t code` |
| Workflow links | account 429/540, analytics 99/540 | printed by any `code/main.py` run |
| Full run statuses | 353 INCOMPLETE / 155 COMPLETE / 10 NOT_APPLICABLE, 0 contract errors | `python3 code/main.py --out $S/full.csv` (then `git checkout -- code/evaluation/last_run_usage.json`) |
| Full run identical to submitted file | yes | `cmp $S/full.csv output.csv` |
| Evidence cited after audit_at | 0 | check script in `research/08_improvement_plan.md` §T8 / `code/tests/test_engine.py` |
| Final run usage | 160 live investigator calls; 171,356 in / 3,602 out; 906 s; intent/vision cached | `code/evaluation/last_run_usage.json` |
| Cold run usage (produced the cached answers) | 29 calls; 40,878 in / 21,867 out; 270 s | `usage_report.md` |
| All calls behind output.csv | 189 calls; 212,234 in / 25,469 out; $0.1319 list, $0.000255 per request | `usage_report.md` |
| Investigator outcome | 40 investigated, 40 `submit_none`, 0 accepted/rejected, 91 over budget | `last_run_usage.json` → `investigator` |
| Cold-run usage by purpose | intent 16 calls (21,220 / 19,833); vision 11 (16,614 / 1,889); escalation 2 (3,044 / 145) | `usage_report.md` |
| Cost estimate (cold run only) | $0.0715, $0.000138 per request (list price); $0 billed | `usage_report.md` (prices from aggregators citing Google's page) |
| Status-filter evidence | COMPLETE↔SUCCESS 20/20, NOT_STARTED↔FAILED 12/12, PARTIALLY_COMPLETE↔PARTIAL\|SUCCESS 5/5 | D24 in `DECISIONS.md` (sample gold accounts joined to their deletion-worker events) |
| Gold findings citing post-request evidence | 41/41 | D19 |
