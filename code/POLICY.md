# POLICY.md — decision policy for the deletion-audit agent

Written before any solver code (2026-10-24, Phase 0). Every later change must cite a
section here or amend it in `DECISIONS.md`. Each rule is tagged with where it lives:
**CODE** (enforced), **PROMPT** (asked of a model, then checked by code), **OPEN** (not yet
decided; see the named switch).

## 1. Output contract (CODE: `ghostaudit/schema.py`)
`output.csv` at the repo root, header `case_id,deletion_status,steps_json`, one row per
request (518), sorted by `case_id`. Each step has exactly
`step_no, phase, system_id, resource_id, operation, state, evidence_id`; step_no runs 1..n.
`validate_row` rejects any row that breaks this; all 32 gold rows pass it.

## 2. Allowed values (CODE: `schema.STATUSES`, `schema.OPERATIONS`), verbatim from problem_statement.md
- status: `COMPLETE`, `INCOMPLETE`, `NOT_APPLICABLE`
- FINDING: `REPORT_REMAINING_RECORD`/PRESENT, `REPORT_EXPOSED_PII`/PRESENT, `REPORT_ACTIVE_DATA_FLOW`/ACTIVE, `REPORT_RESTORE_RISK`/ACTIVE
- REMEDIATION: `DISABLE_DATA_FLOW`/DISABLED, `BLOCK_RESTORE`/BLOCKED, `DELETE_RECORD`/ABSENT, `REDACT_RECORD`/REDACTED, `ANONYMIZE_RECORD`/ANONYMIZED
- VERIFICATION: `VERIFY_RECORD_ABSENT`/ABSENT, `VERIFY_RECORD_PRESENT`/PRESENT, `VERIFY_RECORD_REDACTED`/REDACTED, `VERIFY_RECORD_ANONYMIZED`/ANONYMIZED, `VERIFY_DATA_FLOW_DISABLED`/DISABLED, `VERIFY_RESTORE_BLOCKED`/BLOCKED

No other phase, operation or state may ever be emitted. The state is derived from the operation and never chosen separately.

## 3. Step order (CODE: `schema.canonicalize`; verified 32/32 on the gold samples)
Phase order FINDING → REMEDIATION → VERIFICATION. Within a phase, the operation order above, then
`system_id` ascending, then `resource_id` ascending (switch **S1**). This puts remediation in the safe
order the rules require: data flows are disabled and restores blocked before records are changed.

## 4. Who is the subject (CODE)
- The request email is matched case-insensitively against current emails (`user_identities.csv`)
  **and** historical emails (`user_audit_log.csv` old values). 100 of 518 requests use a historical email.
- The subject's identifier set = `user_id`, every current and historical email, phone and email_hash.
- Normalization: email → trimmed lower-case; phone → digits only; hash → lower-case hex; user_id exact.
- Links require an **exact** normalized identifier match. Names are never enough on their own:
  the evidence contains near-identical decoys (e.g. `x.jr@…` variants, same surname, same first name).

## 5. Time rule (CODE)
- Only evidence with `captured_at ≤ audit_at` may decide or be cited. Per-record times
  (`observed_at`, `recorded_at`) must also be ≤ `audit_at`.
- Per resource, the latest qualifying observation wins.
- `reported_status`, activity `reported_result` and "operation completed" messages are **claims**.
  They can point to a resource; they never prove its state.
- A full snapshot of a collection captured ≤ `audit_at` that lacks the resource proves absence
  (gold: `evidence_54` full profile dump, the empty `customer_profiles` collection in `evidence_51`).

## 6. Resource → rule → required final state (CODE: policy table)
| system_id / prefix | category | rule | required final state |
|---|---|---|---|
| account_service / account_ | USER_PROFILE | rule_01 | ABSENT |
| search_service / search_ | SEARCH_INDEX_ENTRY | rule_03 | ABSENT |
| cache_service / cache_ | CACHE_ENTRY | rule_04 | ABSENT |
| support_platform / support_ | SUPPORT_MESSAGE with shared context | rule_05 | REDACTED |
| support_platform / support_ | SUPPORT_ATTACHMENT owned only by the subject | rule_06 | ABSENT |
| analytics_platform / analytics_ | ANALYTICS_PROFILE, PRODUCT_TELEMETRY | rule_07/08 | ANONYMIZED |
| export_service / export_ | USER_EXPORT | rule_09 | ABSENT |
| (marketing) / marketing_ | MARKETING_PROFILE | rule_10 | ABSENT |
| email_delivery_service / email_delivery_ | EMAIL_DELIVERY_LOG with an email | rule_11 | REDACTED |
| security_service / audit_record_ | SECURITY_AUDIT_LOG within retention | rule_12 | PRESENT (retain) |
| (legal) / legal_hold_ | LEGAL_HOLD_RECORD with active hold | rule_13 | PRESENT (retain) |
| backup_service / backup_ | BACKUP_ARCHIVE | rule_14 | retained, restore BLOCKED |
| identity_service / tombstone_ | ACCOUNT_TOMBSTONE | rule_15 | REDACTED (keep `user_id`, drop name/email/phone/hash) |
| integration_service / data_flow_ | active sync/import path | (recreation rule) | DISABLED |
| ? | SECONDARY_IDENTIFIER | rule_02 | ABSENT (not yet seen in evidence) |

## 7. Per-resource step triples (CODE: planner)
| Required | Observed at audit_at | Finding | Remediation | Verification |
|---|---|---|---|---|
| ABSENT | present | REPORT_REMAINING_RECORD | DELETE_RECORD | VERIFY_RECORD_ABSENT |
| ABSENT | absent | — | — | VERIFY_RECORD_ABSENT |
| REDACTED | identifiable | REPORT_EXPOSED_PII | REDACT_RECORD | VERIFY_RECORD_REDACTED |
| REDACTED | redacted | — | — | VERIFY_RECORD_REDACTED |
| ANONYMIZED | identifiable | REPORT_EXPOSED_PII | ANONYMIZE_RECORD | VERIFY_RECORD_ANONYMIZED |
| ANONYMIZED | anonymized | — | — | VERIFY_RECORD_ANONYMIZED |
| PRESENT (retain) | present | — | — | VERIFY_RECORD_PRESENT |
| DISABLED (flow) | active | REPORT_ACTIVE_DATA_FLOW | DISABLE_DATA_FLOW | VERIFY_DATA_FLOW_DISABLED |
| DISABLED (flow) | disabled | — | — | VERIFY_DATA_FLOW_DISABLED |
| BLOCKED (backup) | restorable | REPORT_RESTORE_RISK | BLOCK_RESTORE | VERIFY_RESTORE_BLOCKED |
| BLOCKED (backup) | blocked | — | — | VERIFY_RESTORE_BLOCKED |

## 8. Status and evidence (CODE)
- `NOT_APPLICABLE` + `[]` when the request does not authorize deletion (§9).
- `COMPLETE` when the plan has no findings. Every verification step cites the latest evidence ≤ `audit_at` that proves that resource's state.
- `INCOMPLETE` otherwise. Findings cite the evidence that proves the violation. Remediation and verification describe future work, so their `evidence_id` is `""`.
- Citable IDs: `evidence_NN`, `activity_NN`, `audit_NN`. Nothing captured after `audit_at`.

## 9. Request intent (PROMPT, checked by CODE)
Deletion = the person asks to delete, erase or close their account or personal data, in any language.
Not deletion: access/copy/export/portability, correction/update (name, phone, email), marketing
opt-out or list removal "only", login help, invoice copies, and anything that says "do not delete"
or "not asking for deletion". The model returns `{"authorizes_deletion": bool, "reason": str}` against
a closed schema. Code checks it against explicit negations; on disagreement or failure → OPEN (S5).

## 10. Conflict table, resolved in advance
| # | Conflict | Resolution |
|---|---|---|
| C1 | Workflow/activity says SUCCESS, evidence ≤ audit_at shows the record present | evidence wins → finding |
| C2 | Evidence after `audit_at` shows the record deleted | ignored; state is as of `audit_at` |
| C3 | Record deleted, then a sync/restore repopulates it before `audit_at` | present + report the active flow / restore risk |
| C4 | Primary absent, replica (read/regional/reporting/failover) still holds the subject | remaining record (replication drift) — **S3** decides the resource/system naming |
| C5 | Two observations at the same time disagree | the one showing data still present wins (conservative) |
| C6 | Identifier is a near miss (`.jr`, different digits, same name) | no link |
| C7 | Request text contains deletion words but asks for something else ("remove me from marketing lists only") | NOT_APPLICABLE |
| C8 | A linked resource has no state evidence ≤ audit_at | **S4** (OPEN) |
| C9 | Support record: shared customer/agent/order context vs attachment owned only by the subject | REDACT vs DELETE per rule_05/rule_06; ownership must be evidenced |
| C10 | Security audit record past its retention date | **S6** (OPEN): rule_12 only covers records within retention |

## 11. Named switches (each a constant in code; current default in bold)
- **S1** resource_id sort: **string** | numeric. Samples can't tell (no adjacent same-op/system pairs).
- **S2** evidence citation for COMPLETE: **latest qualifying file per resource** | one shared file per case.
- **S3** replica drift: report the same `system_id`/`resource_id` as the primary (**default**) | the replica's own name.
- **S4** linked resource with no state evidence: **omit** | report as remaining.
- **S5** intent fallback when the model is unavailable or disagrees with code: **deletion unless an explicit non-deletion pattern matches**.
- **S6** audit record past retention: **treat as RETAIN (verify present)** until evidence shows otherwise.

## 12. What models may and may not decide
Models may: classify request intent (§9); read PDFs and screenshots into typed observations;
propose links for ambiguous resources (investigator agent). Models may **not** emit steps,
statuses, states or evidence IDs that code hasn't verified. Every model-proposed ID must exist in
the parsed evidence, belong to the subject's identifier set, and be dated ≤ `audit_at`.
