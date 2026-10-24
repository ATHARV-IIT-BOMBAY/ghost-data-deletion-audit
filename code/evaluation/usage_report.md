# Usage report — final full-dataset run

This covers the run that produced the submitted `output.csv`: `python3 code/main.py`, started
2026-10-24 22:48:50 IST with **empty model caches**, so every model call was made live.
Numbers are copied from `code/evaluation/last_run_usage.json`, which the run itself writes
(`ghostaudit/llm.py` counts every call and its `usageMetadata`). Nothing below is estimated
except the cost column.

## Run
| | |
|---|---|
| Requests in `output.csv` | 518 (353 INCOMPLETE / 155 COMPLETE / 10 NOT_APPLICABLE) |
| Wall time | 270.4 s (dominated by client-side pacing at 14 requests/min for the free tier) |
| Model provider | Google Gemini API (`generativelanguage.googleapis.com`, v1beta REST) |
| Model calls | 29 live, 0 failed, 0 retries exhausted |
| Ingest problems / rejected vision values | 0 / 4 (ids and file names the validator refused, listed in the run output) |
| Contract-invalid output rows | 0 |

## Per model
| Model | Calls | Input tokens | Output tokens | Total tokens | List price (in / out, per 1M) | Est. cost (USD) |
|---|---:|---:|---:|---:|---|---:|
| `gemini-3.5-flash-lite` | 27 | 37,834 | 21,722 | 59,556 | $0.30 / $2.50 | 0.0657 |
| `gemini-3.5-flash` (escalation only) | 2 | 3,044 | 145 | 3,189 | $1.50 / $9.00 | 0.0059 |
| **Total** | **29** | **40,878** | **21,867** | **62,745** | | **0.0715** |

## Per purpose
| Purpose | Model | Calls | Input | Output | What it does |
|---|---|---:|---:|---:|---|
| intent | flash-lite | 16 | 21,220 | 19,833 | classifies each distinct request text (deletion or not), 25 texts per call, multilingual |
| vision | flash-lite | 11 | 16,614 | 1,889 | transcribes the 11 admin-console screenshots (one call each) |
| vision_escalation | flash | 2 | 3,044 | 145 | re-asks a screenshot when the lite model saw a person but returned no resource ids |

Most output tokens are intent: the model writes a one-line reason per request, quoting the deciding phrase, which makes each classification auditable.

## Per request
| | |
|---|---:|
| Total tokens / request | 62,745 / 518 = **121.1** |
| Input tokens / request | 78.9 |
| Output tokens / request | 42.2 |
| Model calls / request | 0.056 |
| Est. cost / request at list price | **$0.000138** |
| Actually billed | **$0.00** (free tier) |

The intent pass classifies every distinct request text in both request files (eval + sample, 402 distinct texts), so the same cache also serves the evaluation runs. Per-request figures divide by the 518 submitted rows.

## Why so few calls
- **The deterministic engine needs no model** for ingestion (SQL/JSON/logs/TXT/PDF), identity, linking, state at `audit_at`, policy or planning. Models are used only where reading is required.
- **Batching:** 25 request texts per intent call instead of one call per request (518 → 16 calls).
- **Caching:** answers are stored under `code/cache/` by content hash (sha256 of image bytes or text + prompt version). A repeat run makes **0** calls and reproduces the same output. Checked: the cached rerun matched the cold run on all 518 rows.
- **Escalation only on demand:** the more expensive model ran 2 times, not 11.

## Pricing note
List prices are per 1M tokens for the standard paid tier, taken from public pricing aggregators that cite Google's Gemini API pricing page (eesel.ai, pricepertoken.com, anotherwrapper.com, checked 2026-10-24). They were not verified against Google's own page. The run used the free tier: limits 15 requests/min, 250K tokens/min, 500 requests/day, so the pipeline paces itself to 14 requests/min.
