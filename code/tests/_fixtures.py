"""Shared paths and loaders for the tests (dataset files only, never organizer data)."""

from __future__ import annotations

import csv
import json
from pathlib import Path

DATASET = Path(__file__).resolve().parents[2] / "dataset"


def rows(name: str) -> dict[str, dict]:
    with open(DATASET / name, newline="", encoding="utf-8") as f:
        return {r["case_id"]: r for r in csv.DictReader(f)}


def gold() -> dict[str, tuple[str, list]]:
    return {c: (r["deletion_status"], json.loads(r["steps_json"])) for c, r in rows("sample_output.csv").items()}


def all_requests() -> dict[str, dict]:
    return {**rows("deletion_requests.csv"), **rows("sample_deletion_requests.csv")}
