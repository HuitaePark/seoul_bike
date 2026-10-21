"""Explicit per-request runtime configuration, outside the Kafka contract."""
from __future__ import annotations

import json
from pathlib import Path

from .contract import SolveContext, _timestamp


def reject_constant(value):
    raise ValueError(f"non-JSON number: {value}")


class Contexts:
    def __init__(self, rows: dict):
        if not isinstance(rows, dict) or not rows:
            raise ValueError("runtime requests must be a non-empty object keyed by requestId")
        self._rows = {}
        for request_id, settings in rows.items():
            if not isinstance(request_id, str) or not request_id.strip() or not isinstance(settings, dict):
                raise ValueError("invalid runtime request entry")
            shift_id = settings.get("shiftId")
            if not isinstance(shift_id, str) or not shift_id.strip():
                raise ValueError("runtime shiftId is required for every request")
            depot = settings.get("depot")
            if not isinstance(depot, list) or len(depot) != 2:
                raise ValueError("runtime depot must be [latitude, longitude]")
            context = SolveContext(tuple(depot), _timestamp("runtime asOf", settings.get("asOf")))
            self._rows[request_id] = (shift_id, context)

    @classmethod
    def load(cls, path: str | Path):
        document = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=reject_constant)
        if not isinstance(document, dict) or set(document) != {"requests"}:
            raise ValueError("runtime file must contain a requests object; it is not a Kafka payload")
        return cls(document["requests"])

    def resolve(self, payload: dict) -> SolveContext:
        entry = self._rows.get(payload["requestId"])
        if entry is None or entry[0] != payload["shiftId"]:
            raise ValueError("explicit runtime context missing for this requestId/shiftId; input remains uncommitted")
        return entry[1]
