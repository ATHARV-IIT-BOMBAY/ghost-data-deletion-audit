# Usage report — final full-dataset run

The submitted `output.csv` was produced by `python3 code/main.py` on 2026-10-25 (IST). Numbers are
copied from `code/evaluation/last_run_usage.json`, which the run itself writes (`ghostaudit/llm.py`
counts every call and its `usageMetadata`). Model answers that were already cached were produced
by an earlier **cold run** (all caches empty, 2026-10-24 22:48 IST); both are reported, so the table
covers every model call behind the submitted file. Only the cost column is an estimate.

## The final run
| | |
|---|---|
| Requests in `output.csv` | 518 (353 INCOMPLETE / 155 COMPLETE / 10 NOT_APPLICABLE), 0 contract violations |
| Wall time | 906 s (pacing at 14 requests/min for the free tier) |
| Model provider | Google Gemini API (`generativelanguage.googleapis.com`, v1beta REST) |
| Live model calls | **160** (all from the evidence-investigator agent), 0 failed |
| Served from cache | 409 cache hits (every distinct request text for intent + the 11 screenshots), 0 calls |
| Investigator | 40 investigations (budget `MAX_INVESTIGATIONS`), 40 × `submit_none`, 0 accepted, 0 rejected, 91 targets skipped over budget |
| Rerun of the same command | **0 calls**, byte-identical `output.csv` (the investigator budget counts cached investigations) |

## All model calls behind `output.csv`
| Run | Model | Purpose | Calls | Input tokens | Output tokens |
|---|---|---|---:|---:|---:|
| final | `gemini-3.5-flash-lite` | investigator (tool-calling loop) | 160 | 171,356 | 3,602 |
| cold (cached answers) | `gemini-3.5-flash-lite` | intent (25 texts per call) | 16 | 21,220 | 19,833 |
| cold (cached answers) | `gemini-3.5-flash-lite` | vision (11 screenshots) | 11 | 16,614 | 1,889 |
| cold (cached answers) | `gemini-3.5-flash` | vision escalation | 2 | 3,044 | 145 |
| **Total** | | | **189** | **212,234** | **25,469** |

## Per model and cost
| Model | Calls | Input | Output | Total tokens | List price (in / out per 1M) | Est. cost (USD) |
|---|---:|---:|---:|---:|---|---:|
| `gemini-3.5-flash-lite` | 187 | 209,190 | 25,324 | 234,514 | $0.30 / $2.50 | 0.1261 |
| `gemini-3.5-flash` | 2 | 3,044 | 145 | 3,189 | $1.50 / $9.00 | 0.0059 |
| **Total** | **189** | **212,234** | **25,469** | **237,703** | | **0.1319** |

Of this, the final run alone: 160 calls, 174,958 tokens, $0.0604.

## Per request (518 rows)
| | |
|---:|---:|
| Total tokens / request | 237,703 / 518 = **458.9** |
| Input / output tokens per request | 409.7 / 49.2 |
| Model calls / request | 0.365 |
| Est. cost / request at list price | **$0.000255** |
| Actually billed | **$0.00** (free tier) |

## Where the tokens go and why
- **Investigator, 74% of tokens:** each turn re-sends the conversation including search results, which are mostly input tokens. It's capped at 4 turns per record and 40 records per run. On this dataset it found no extra state evidence (40/40 `submit_none`), consistent with the parsers already capturing every visible state; see the ablation in `evaluation_report.md`.
- **Intent:** batching 25 texts per call (402 texts → 16 calls). Most output tokens are the per-request reason, which keeps every classification auditable.
- **Vision:** one call per screenshot; the stronger model only when the cheap one misses ids (2 of 11).
- **The deterministic engine needs no model** for ingestion, identity, linking, state, policy or planning.
- **Caching** by content hash means reruns cost 0 calls and reproduce the output exactly (checked twice: cold vs warm, and the final vs a rerun).

## Pricing note
List prices are per 1M tokens for the standard paid tier, from public pricing aggregators citing Google's Gemini API pricing page (eesel.ai, pricepertoken.com, anotherwrapper.com, checked 2026-10-24); not verified against Google's own page. The runs used the free tier (15 requests/min, 500/day), so the client paces itself to 14 requests/min.
