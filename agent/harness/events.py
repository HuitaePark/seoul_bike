# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Progress events (TOKEN ... FAILED) and the sink the agent server forwards to `ai.events`.

"""Progress events (docs/HARNESS.md section 4)."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

EventType = Literal["TOKEN", "TOOL_START", "TOOL_END", "APPROVAL_REQUIRED", "DONE", "FAILED"]


@dataclass(frozen=True)
class ProgressEvent:
    type: EventType
    task_id: str
    trace_id: str
    data: dict[str, Any] = field(default_factory=dict)
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> dict[str, Any]:
        """Flat JSON for `ai.events`: common fields plus event-specific fields."""
        body = asdict(self)
        return {**{k: v for k, v in body.items() if k != "data"}, **self.data}


class EventSink(Protocol):
    """Kafka producer in the agent server, a list in tests and evaluation."""

    def emit(self, event: ProgressEvent) -> None: ...


class ListSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)

    def of(self, type_: EventType) -> list[ProgressEvent]:
        return [e for e in self.events if e.type == type_]
