"""system_activity.csv: a global event stream. Results are claims; events can only point at resources."""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from .models import idents

_LINK = re.compile(r"linked (\S+) \(.*?\) to (\S+) and (user_\d+)")
_OWNS = re.compile(r"confirmed (\S+) \(.*?\) belongs only to (\S+) and (user_\d+)")


@dataclass(frozen=True)
class Activity:
    activity_id: str
    event_time: str
    event_type: str
    source_system: str
    target_system: str
    resource_id: str
    reported_result: str
    details: str


def load_activity(dataset: Path) -> list[Activity]:
    with open(dataset / "system_activity.csv", newline="", encoding="utf-8") as f:
        return [Activity(**r) for r in csv.DictReader(f)]


def explicit_links(events: list[Activity]) -> list[tuple[Activity, str, frozenset[str], bool]]:
    """Data-catalog link events: (event, resource_id, identifiers, sole_owner)."""
    out = []
    for a in events:
        for rx, sole in ((_LINK, False), (_OWNS, True)):
            m = rx.search(a.details)
            if m:
                out.append((a, m.group(1), idents(m.group(2).rstrip(";,."), m.group(3)), sole))
    return out
