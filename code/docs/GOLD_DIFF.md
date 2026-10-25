# GOLD_DIFF.md — every sample case that is not an exact match

Source: `python3 code/evaluation/main.py --pred $S/s.csv -v` plus the decision trace (`--trace`).
20 of 32 cases are exact. The other 12 differ by 15 missing and 1 extra verification step.
**No finding, remediation or status is wrong.**

| Case | Diff | Cause | Where |
|---|---|---|---|
| case_03 | − VERIFY_RECORD_ANONYMIZED analytics_9406 (evidence_94) | anonymized analytics carry no identifier; its workflow event shares its time window with another case's | `engine.py:171` `_assign` (analytics: event-side elimination only) |
| case_21 | − VERIFY_RECORD_ANONYMIZED analytics_5335 (evidence_92) | same | same |
| case_25 | − VERIFY_RECORD_ANONYMIZED analytics_1895 (evidence_92) | same | same |
| case_26 | − VERIFY_RECORD_ANONYMIZED analytics_4566 (evidence_92) | same | same |
| case_27 | − VERIFY_RECORD_ANONYMIZED analytics_4574 (evidence_92) | same | same |
| case_28 | − VERIFY_RECORD_ABSENT account_5482 (evidence_92); − VERIFY_RECORD_ANONYMIZED analytics_6077 | account tie: cases share `reported_at` and compatible results; plus the analytics tie | `engine.py:171`, `STATUS_RESULTS` `engine.py:62` |
| case_04 | − VERIFY_RECORD_ABSENT account_004 (evidence_54) | account tie survives the status filter | same |
| case_23 | − VERIFY_RECORD_ABSENT account_410 | account tie | same |
| case_29 | − VERIFY_RECORD_ABSENT account_7012 | account tie | same |
| case_18 | − VERIFY_RECORD_PRESENT audit_record_6645 | no evidence row ties this audit record to the subject | `engine.py:120` `link` |
| case_20 | − VERIFY_RECORD_PRESENT audit_record_5569 | same | same |
| case_17 | + VERIFY_RECORD_REDACTED tombstone_17 | a data-catalog event (`activity_4011`) links it to the subject but gives no state, so it becomes a future check; gold leaves it out | `engine.py:292` `future_checks`, switch S4 |

**Why not guess the ties.** A tie between k cases is right 1/k of the time. Under a per-step grader, a wrong guess costs a false positive for the case that got it and still misses the case that should have. The output stays honest: every emitted step is supported.
