# Ghost Data Still Exists — deletion-audit agent

HackerRank Orchestrate, October 2026. For each deletion request, the agent decides whether
deletion was actually requested, works out whether the person's data is really gone at
`audit_at`, and writes the ordered findings, remediation and verification steps
(`output.csv`: `case_id,deletion_status,steps_json`).

**Results on the 32 labelled samples:** status accuracy 1.000 · step F1 0.946 (P 0.994, R 0.902) ·
holdout F1 0.927 · every finding and remediation step correct · 0 contract violations · 6/6
custom cases. The final 518-request run made 29 Gemini calls (62.7K tokens, ~$0.07 at list price,
$0 billed on the free tier). Details: `code/evaluation/evaluation_report.md`, `code/evaluation/usage_report.md`.

## How it works

```
request ──► intent ──────────► NOT_APPLICABLE  (rules first; Gemini for other languages/phrasings)
   │
   ▼
identity      email → user_id → every current + historical email, phone, hash   (exact match only)
   │
evidence      94 files → one Observation table
   │            SQL (sqlite3) · JSON · 6 log syntaxes · TXT · PDF (pypdf) · PNG (Gemini vision)
   ▼
linking       identifier matches + data-catalog events + deletion-workflow events
   │            (workflow events assigned to cases globally by elimination; ties left unassigned)
   ▼
state         latest evidence ≤ audit_at per resource; claims (statuses, "completed") never count;
   │            absence proven by full listings; replica drift; findings need post-request evidence
   ▼
policy        deletion_rules.csv → required final state (delete / redact / anonymize / retain /
   │            block restore / disable flow; tombstones keep only user_id)
   ▼
planner       finding → remediation → verification per resource, canonical order, validated
```

**Where the model decides and where code enforces.** Models only *read*: request intent and
screenshots. Every model answer goes through code:
- intent: explicit "do not delete" wording always wins, and the model can only make a request non-deletion at confidence ≥ 0.8;
- vision: the model returns ids, identifiers and the status words it sees; code maps status to state, rejects ids that aren't valid resource ids or that differ from a known id only by zero-padding, and escalates to a stronger model once if ids are missing.

Statuses, steps, states and evidence ids are produced only by deterministic code.
`code/POLICY.md` is the rulebook (written before any code); `code/DECISIONS.md` is the
timestamped decision log (D1–D23) with the measurement behind each change.

## Setup

Requirements: **Python 3.11+** (developed on 3.14) and `pypdf`. Everything else is standard library
(csv, json, sqlite3, urllib).

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r code/requirements.txt
```

Model access (optional; see "Without a key"):

```bash
cp code/.env.example .env           # then put your key in .env — never commit it
```

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `GEMINI_API_KEY` | only for uncached model calls | — | Google Gemini API key (free tier is enough) |
| `GEMINI_MODEL` | no | `gemini-3.5-flash-lite` | main model for intent + vision |

`.env` is read from the repository root (next to this README), or the variables can be
exported in the shell.

## Run

From the repository root (the folder that contains `code/` and `dataset/`):

```bash
python3 code/main.py                       # all 518 requests → ./output.csv
```

| Flag | Effect |
|---|---|
| `--requests PATH` | another request file, e.g. `dataset/sample_deletion_requests.csv` |
| `--out PATH` | where to write the CSV (default `./output.csv`) |
| `--trace PATH` | JSONL decision trace per case: subject, every linked resource, why, state and evidence |
| `--no-models` | deterministic layer only (ablation; makes no API calls) |

Each run also writes `code/evaluation/last_run_usage.json` (calls, tokens, cache hits per model and purpose).

**Without a key.** Model answers are cached in `code/cache/` (keyed by sha256 of the input +
prompt version) and ship with the code, so `python3 code/main.py` reproduces the submitted
`output.csv` with **zero API calls**. With no key and no cache, screenshots are skipped (and
reported) and intent falls back to the rules; the run still completes with a valid file.

## Evaluate

```bash
python3 code/main.py --requests dataset/sample_deletion_requests.csv --out sample_pred.csv
python3 code/evaluation/main.py --pred sample_pred.csv -v       # --split dev | holdout
python3 code/evaluation/main.py --baseline reported              # no-model baselines
python3 code/evaluation/custom_cases.py                          # 6 custom cases
```

## Code map

| File | Role |
|---|---|
| `code/main.py` | entry point: load, prepare models, assign workflow links, audit every case, write CSV + usage |
| `code/ghostaudit/schema.py` | allowed values, canonical step order, output-contract validator (single source of truth) |
| `code/ghostaudit/models.py` | `Observation` / `Coverage` types, identifier normalization |
| `code/ghostaudit/ingest/` | `sql.py`, `json_records.py`, `logs.py`, `documents.py` (TXT/PDF), `media.py` (screenshots via Gemini) |
| `code/ghostaudit/identity.py` | request email → subject with all current and historical identifiers |
| `code/ghostaudit/intent.py` | deletion-or-not: negation rules + batched, cached Gemini classifier |
| `code/ghostaudit/activity.py` | activity stream loader, data-catalog link events |
| `code/ghostaudit/engine.py` | linking, workflow assignment, state at `audit_at`, plan |
| `code/ghostaudit/policy.py` | rule table → required final state and step triple |
| `code/ghostaudit/llm.py` | Gemini REST client: schema-constrained JSON, classified retries, rate pacing, usage accounting |
| `code/evaluation/main.py` | scorer (written before the solver), baselines, dev/holdout |
| `code/evaluation/custom_cases.py` | custom evaluation cases |
| `code/POLICY.md`, `code/DECISIONS.md` | rulebook and decision log |

## Known limits
- When several cases report at exactly the same time and their accounts carry no identifier, the
  data cannot say which account is whose; those links are left out rather than guessed. This is
  the main source of missed verification steps.
- `resource_id` ordering inside a phase assumes string order (the samples never disambiguate).
- Vision output was checked by eye on 2 screenshots; no labelled case cites a screenshot.

## Packaging

```bash
zip -r code.zip README.md code -x "*/__pycache__/*" "*/.venv/*" "*/venv/*" "*/.DS_Store" "*.env"
```
