"""Gemini REST client (stdlib only) with classified retries and per-call usage accounting.

Every call is recorded in `USAGE` (provider, model, tokens, cache hits, failures) so the
usage report is generated from the run, never written by hand. The key is read from the
environment (`GEMINI_API_KEY`, optionally via an uncommitted `.env` at the repo root).
"""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

PROVIDER = "google-gemini"
DEFAULT_MODEL = "gemini-3.5-flash-lite"   # free tier: 15 requests/min, 250K tokens/min, 500/day
MIN_INTERVAL_S = 60 / 14                  # stay under the 15 RPM free-tier limit
_last_call = 0.0
API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_ATTEMPTS = 4          # transient failures only (429 / 5xx / network)
TIMEOUT_S = 120
TRANSIENT = {408, 429, 500, 502, 503, 504}


class LLMUnavailable(RuntimeError):
    """No key configured, or a permanent error (auth, bad request): never retried."""


@dataclass
class Usage:
    calls: int = 0
    cache_hits: int = 0
    failures: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    per_model: dict[str, dict[str, int]] = field(default_factory=dict)
    per_purpose: dict[str, dict[str, int]] = field(default_factory=dict)

    def record(self, model: str, inp: int, out: int, purpose: str = "other") -> None:
        self.calls += 1
        self.input_tokens += inp
        self.output_tokens += out
        m = self.per_model.setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        m["calls"] += 1
        m["input_tokens"] += inp
        m["output_tokens"] += out
        p = self.per_purpose.setdefault(purpose, {"calls": 0, "input_tokens": 0, "output_tokens": 0})
        p["calls"] += 1
        p["input_tokens"] += inp
        p["output_tokens"] += out

    def as_dict(self) -> dict:
        return {"provider": PROVIDER, "calls": self.calls, "cache_hits": self.cache_hits,
                "failures": self.failures, "input_tokens": self.input_tokens,
                "output_tokens": self.output_tokens, "per_model": self.per_model, "per_purpose": self.per_purpose}


USAGE = Usage()


def _load_dotenv() -> None:
    env = Path(__file__).resolve().parents[2] / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            key, sep, val = line.partition("=")
            if sep and key.strip() and not key.strip().startswith("#"):
                os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def model_name() -> str:
    _load_dotenv()
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def available() -> bool:
    _load_dotenv()
    return bool(os.environ.get("GEMINI_API_KEY"))


ESCALATION_MODEL = "gemini-3.5-flash"  # stronger model, used only when the lite answer is empty


def generate_json(parts: list[dict], schema: dict, system: str, model: str | None = None,
                  purpose: str = "other") -> dict:
    """One structured call: returns the parsed JSON object the model produced.

    `parts` are Gemini content parts (text / inline_data). `schema` constrains the output
    (responseSchema), so a malformed answer is an API-level failure, not a parsing guess.
    """
    _load_dotenv()
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise LLMUnavailable("GEMINI_API_KEY is not set")
    model = model or model_name()
    body = json.dumps({
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json",
                             "responseSchema": schema},
    }).encode()
    req = urllib.request.Request(API.format(model=model), data=body, method="POST",
                                 headers={"Content-Type": "application/json", "x-goog-api-key": key})
    global _last_call
    for attempt in range(1, MAX_ATTEMPTS + 1):
        wait = _last_call + MIN_INTERVAL_S - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call = time.time()
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as e:
            if e.code not in TRANSIENT or attempt == MAX_ATTEMPTS:
                USAGE.failures += 1
                detail = e.read().decode(errors="replace")[:300]
                raise LLMUnavailable(f"HTTP {e.code} from {model}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == MAX_ATTEMPTS:
                USAGE.failures += 1
                raise LLMUnavailable(f"network error: {e}") from e
        time.sleep(min(60, 2 ** attempt + random.random() * 2))  # backoff with jitter
    meta = payload.get("usageMetadata", {})
    USAGE.record(model, meta.get("promptTokenCount", 0), meta.get("candidatesTokenCount", 0), purpose)
    try:
        text = payload["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        USAGE.failures += 1
        raise LLMUnavailable(f"unusable response from {model}: {str(payload)[:300]}") from e
