"""Evidence ingestion: every file in evidence_files.csv → Observation / Coverage rows.

One parser module per format family; each exposes
    parse(path: Path, file_id: str, captured_at: str) -> tuple[list[Observation], list[Coverage]]
"""

from __future__ import annotations

import csv
import importlib
from pathlib import Path

from ..models import Coverage, Observation

PARSERS = {
    ".sql": "sql",
    ".json": "json_records",
    ".log": "logs",
    ".txt": "documents",
    ".pdf": "documents",
    ".png": "media",  # vision model, cached; skipped (and reported) when no key and no cache
}


def load_evidence(dataset: Path) -> tuple[list[Observation], list[Coverage], list[str]]:
    """Parse every manifest file. Returns observations, coverage, and per-file problems."""
    obs: list[Observation] = []
    cov: list[Coverage] = []
    problems: list[str] = []
    with open(dataset / "evidence_files.csv", newline="", encoding="utf-8") as f:
        manifest = list(csv.DictReader(f))
    # Text evidence first: its resource ids (plus the activity stream's) are the vocabulary the
    # screenshot reader is checked against.
    manifest.sort(key=lambda r: r["file_path"].lower().endswith(".png"))
    known_ready = False
    for row in manifest:
        path = dataset / row["file_path"]
        if path.suffix.lower() == ".png" and not known_ready:
            from . import media
            with open(dataset / "system_activity.csv", newline="", encoding="utf-8") as f:
                media.KNOWN_IDS = {o.resource_id for o in obs} | {r["resource_id"] for r in csv.DictReader(f)}
            known_ready = True
        mod = PARSERS.get(path.suffix.lower())
        if mod is None:
            problems.append(f"{row['file_id']} {row['file_path']}: no parser for this format")
            continue
        try:
            parser = importlib.import_module(f"{__name__}.{mod}")
            o, c = parser.parse(path, row["file_id"], row["captured_at"])
            obs += o
            cov += c
        except Exception as e:  # one bad file must not sink the run; it is reported
            problems.append(f"{row['file_id']} {row['file_path']}: {type(e).__name__}: {e}")
    return obs, cov, problems
