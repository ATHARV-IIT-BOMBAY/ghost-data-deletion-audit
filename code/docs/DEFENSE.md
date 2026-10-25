# DEFENSE.md — interview brief

Every number below was re-measured on the final code (see `NUMBERS.md` for the commands).
Habit for every answer: **say the measurement, name the file and line, name the parameter.**

## The 90-second table
| If asked about | The number | Where (file:line) | Parameter / constant |
|---|---|---|---|
| Overall accuracy | samples: status **1.000**, step F1 **0.965** (P 0.995, R 0.938), row exact 0.625; dev F1 0.969; **holdout F1 0.952** | scorer `code/evaluation/main.py:94` (`score`) | holdout = every 4th case, `HOLDOUT_EVERY` `main.py:33` |
| Versus baselines | trust `reported_status` → status 0.531; always INCOMPLETE → 0.688; both step F1 0 | `evaluation/main.py:60` (`baseline`) | — |
| Why deterministic | all 9 finding/remediation operations F1 **1.00**; misses are verification steps only | `engine.py:253` (`audit`), `policy.py:11` (`TREATMENTS`) | — |
| Output contract | 0 contract violations on 32 samples and 518 rows; all 32 gold rows pass the validator | `schema.py:77` (`validate_row`), `schema.py:59` (`canonicalize`) | `RESOURCE_SORT` `schema.py:42` (affects 0 rows) |
| Linking people to records | exact normalized identifiers + catalog links + workflow events | `engine.py:120` (`link`), `models.py:91` (`ident`) | — |
| Ties | account links 312 → **429** after the status filter; F1 0.946 → 0.965 | `engine.py:171` (`_assign`), `engine.py:62` (`STATUS_RESULTS`) | `WORKFLOW_EVENTS` `engine.py:54` (window −10..80 min) |
| Time rule | 0 cited evidence after `audit_at` (518 rows); custom case C2 | `engine.py:116` (`_visible`) | stale-finding rule `engine.py:298` |
| Replica drift | counts only if the same snapshot shows primary clean and replica exposed | `engine.py:227` (`state_at`), `engine.py:33` (`location`) | — |
| Intent model | custom cases 3/6 → **6/6**; first prompt regressed sample status to 0.844, fixed | `intent.py:63` (`IntentClassifier`), `intent.py:100` | `MODEL_MIN_CONFIDENCE=0.8` `intent.py:36`, `BATCH_SIZE=25` `intent.py:37` |
| Vision guards | caught `audit_record_3` (for `_003`) and invented `marketing_4`; escalation recovered `marketing_001` | `media.py:112` (`_padding_error`), `media.py:98` (escalation) | `ESCALATION_MODEL` `llm.py:84` |
| Cost | all calls behind `output.csv`: **189 calls, 212,234 in / 25,469 out tokens**, about $0.13 at list price, $0 billed; rerun 0 calls | `llm.py:34` (`Usage`) → `code/evaluation/last_run_usage.json` | `DEFAULT_MODEL` `llm.py:20`; pacing `MIN_INTERVAL_S` `llm.py:21` |
| Reliability | retries only for 408/429/5xx/network, max 4; one bad file or case can't sink the run | `llm.py:24-26`, `ingest/__init__.py:25` (`load_evidence`), `main.py` per-case try | `MAX_ATTEMPTS`, `TRANSIENT` |
| Reproducibility | cold and cached runs byte-identical; the zip with no key reproduces `output.csv` | `media.py:85` (`_transcribe`), `intent.py:73` (`prepare`) | cache key = sha256(input + `PROMPT_VERSION` `media.py:61`) |
| Tests | 31 unit tests, 0.42 s, no key; mutation-checked | `code/tests/` | — |
| Agent (tool loop) | 40 investigations on the full run, all `submit_none`, 0 rows changed; on samples its 6 targets only have evidence after `audit_at`, so `submit_none` is correct | `agent.py` (`Investigator.investigate`, `_verify`) | `MAX_TOOL_ITERATIONS=4`, `MAX_INVESTIGATIONS=40` |

## 30-second pitch
"Workflow dashboards say *Complete*; we check whether that's true. The agent parses all 94 evidence files (SQL, JSON, six log syntaxes, text, PDF and screenshots) into one observation table. It resolves each requester to every current and past identifier, links their records, and reconstructs each record's state at the audit time, ignoring later evidence and treating workflow messages as claims. It then applies the deletion policy and emits ordered findings, fixes and checks. Code makes every decision; Gemini only reads what code can't: multilingual request intent and screenshots. On the labelled samples: 100% status accuracy, 0.965 step F1, 0.952 on holdout, every finding and fix correct, for about seven cents of model calls."

## Modules, one line each
| File | Role |
|---|---|
| `main.py` | entry point: load, prepare models, assign workflow links, audit every case, write CSV and usage |
| `ghostaudit/schema.py` | allowed values, canonical step order, output-contract validator |
| `ghostaudit/models.py` | `Observation`/`Coverage` types, identifier and time normalization |
| `ghostaudit/ingest/sql.py` | sqlite3 in memory; inventory, topology (primary + replicas) and flat dumps; one Coverage per table |
| `ghostaudit/ingest/json_records.py` | snapshot bundles, service exports, ledgers, store captures; Coverage only for complete listings |
| `ghostaudit/ingest/logs.py` | one tolerant tokenizer for 6 syntaxes; joins `*_started` (lookup key) to `*_completed` (resource, rows/status) by trace id |
| `ghostaudit/ingest/documents.py` | handover TXT rows; PDFs via pypdf (attachment, retention register, compliance journal) |
| `ghostaudit/ingest/media.py` | screenshots: model transcribes, code maps status → state, guards ids, escalates once, caches |
| `ghostaudit/identity.py` | email → user → all current and historical identifiers |
| `ghostaudit/intent.py` | negation rules + batched, cached Gemini classifier with a confidence gate |
| `ghostaudit/activity.py` | activity stream, data-catalog link events |
| `ghostaudit/engine.py` | link, workflow assignment, state at `audit_at`, plan |
| `ghostaudit/policy.py` | rule table → required final state → finding/remediation/verification |
| `ghostaudit/agent.py` | bounded tool-calling investigator (search/read/submit) with code-verified quotes |
| `ghostaudit/llm.py` | Gemini REST: schema-constrained JSON and function calling, classified retries, pacing, usage accounting |

## Five decisions, each led by its measurement
1. **Scorer first** (D3). Gold against itself scores 1.000; 4 injected faults were all caught. Every later change was judged by it, which is why I can quote every number.
2. **Scoped absence, then a drift rule** (D8, D9). Treating per-subject bundles as full listings dropped status accuracy 0.969 → 0.906. Scoping plus the same-snapshot drift rule brought it to 1.000.
3. **Workflow linking + status filter** (D13, D24). Accounts with no identifier are linked by their own deletion event. The case's claimed status matches its event result 37/37 in the samples, so it's used only to *rule out* candidates. F1 0.920 → 0.943 → 0.965; true ties stay unassigned.
4. **Vision: model transcribes, code decides** (D16, D18). I checked the extractions against the images and found 2 model errors, which became code guards and an escalation path.
5. **Intent model with a gate** (D22). Rules failed the Spanish, French and negated-English custom cases. The model fixed them, but its first prompt dropped sample status to 0.844 by calling "confirm my deletion is done" non-deletion. A prompt definition fixed it; rules still win, and the model needs ≥ 0.8 confidence.

## Say these before the judge finds them
- **The remaining misses are linking, not logic.** 15 verification steps over 12 cases: records with no identifier where several cases report at the same moment. I don't guess (`GOLD_DIFF.md`).
- **Vision can't be scored on labels**: no sample cites a screenshot. Removing all models leaves the sample score unchanged (0.965); the models' measured value is the custom cases (3/6 → 6/6), 10 changed eval rows each traced to a post-request screenshot, and 0 invalid rows.
- **n = 32 labels, holdout 8**: one holdout case is 12.5 points.
- **AI wrote the four parsers** as sub-agents under my interface contract and gold-citation checks. I verified them, then stopped using sub-agents because of cost.
- **Only the Gemini free tier was available** (Bedrock denied by IAM), so the system is designed to produce a full valid output with zero model calls.
- **Retention dates are parsed but not compared** (switch S6). **The investigator agent found nothing new** (40/40 `submit_none`): it is a bounded, verified safety net, not a source of gains on this data.

## If asked about production
Monitor: share of cases with an unlinked primary account; contract-validator failures (must stay 0); model/rule disagreements and vision rejections; status-distribution drift; cache-miss rate and cost per request. Escalate low-confidence or unlinkable cases to a human queue instead of guessing.
