# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Progress events in the contract format (docs/specs TaskEvent + Kafka envelope) and the sink interface.

"""Progress events (docs/HARNESS.md section 4, docs/specs openapi `TaskEvent`, asyncapi `TaskEventMessage`)."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from .trace import TraceEvent

EventType = Literal["TOKEN", "TOOL_START", "TOOL_END", "APPROVAL_REQUIRED", "DONE", "FAILED"]
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ProgressEvent:
    """One `ai.events` message.

    `body` holds the contract fields for the event type (`token`, `tool`, `approval`, `answer`·`sources`,
    `error`). `harness` holds extensions the contract does not define yet (failure code, run id, counters);
    consumers ignore unknown fields, so they travel under one `harness` key.
    """

    type: EventType
    task_id: str
    trace_id: str
    shift_id: str
    body: dict[str, Any] = field(default_factory=dict)
    harness: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    occurred_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_json(self) -> dict[str, Any]:
        """`TaskEventMessage`: Kafka envelope + `TaskEvent` without `seq` (+ `harness` extensions). The backend
        numbers events per task (`seq`) when it stores them and uses that as the SSE id."""
        message = {
            "eventId": self.event_id,
            "schemaVersion": SCHEMA_VERSION,
            "occurredAt": self.occurred_at,
            "shiftId": self.shift_id,
            "traceId": self.trace_id,
            "taskId": self.task_id,
            "type": self.type,
            **self.body,
        }
        if self.harness:
            message["harness"] = self.harness
        return message


class EventSink(Protocol):
    """Kafka producer in the agent server, a list in tests and evaluation."""

    def emit(self, event: ProgressEvent) -> None: ...


class ListSink:
    """Progress events and the decision trace (harness/trace.py) in lists, for tests and evaluation."""

    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self.traces: list[TraceEvent] = []

    def emit(self, event: ProgressEvent) -> None:
        self.events.append(event)

    def trace(self, event: TraceEvent) -> None:
        self.traces.append(event)

    def of(self, type_: EventType) -> list[ProgressEvent]:
        return [e for e in self.events if e.type == type_]
