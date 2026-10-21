# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Task input, task outcome, and the per-segment runtime context every middleware reads.

"""Task, outcome, and runtime context."""
from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from .events import EventSink, EventType, ProgressEvent
from .guardrails import Masker
from .trace import NullTrace, TraceEvent, TraceKind, TraceSink, TraceSource

TaskType = Literal["plan_draft", "field_report", "manager_request", "qa"]


@dataclass(frozen=True)
class Requester:
    """Who created the task (from the backend's authenticated user, never from model output)."""

    role: Literal["manager", "driver", "executive", "scheduler"]
    truck_id: str | None = None  # 기사면 필수: 기사는 자기 트럭만 조회·제안한다 (NFR-06)

    def __post_init__(self) -> None:
        if self.role == "driver" and not self.truck_id:
            raise ValueError("driver requester needs truck_id")


@dataclass(frozen=True)
class FieldReport:
    """A driver report as the backend stores it (docs/specs internal-api `FieldReport`). Bike counts come from
    the driver app, so they are the only counts a model may use for an inventory constraint (NFR-07)."""

    report_id: str
    truck_id: str
    station_id: str
    actual_bikes: int
    loaded: int = 0
    unloaded: int = 0
    issue: Literal["BLOCKED", "CONSTRUCTION", "INVENTORY_MISMATCH", "OTHER"] | None = None
    memo: str | None = None
    reported_at: str | None = None  # 보고 시각 (internal-api FieldReport.reportedAt). 제약 valid_from의 근거

    @classmethod
    def from_contract(cls, data: dict) -> FieldReport:
        return cls(
            report_id=data["reportId"],
            truck_id=data["truckId"],
            station_id=data["stationId"],
            actual_bikes=int(data["actualBikes"]),
            loaded=int(data.get("loaded") or 0),
            unloaded=int(data.get("unloaded") or 0),
            issue=data.get("issue"),
            memo=data.get("memo"),
            reported_at=data.get("reportedAt"),
        )

    def render(self) -> str:
        """Code-written facts for the model. The free-text memo is passed separately as untrusted input."""
        parts = [f"보고 {self.report_id}", f"트럭 {self.truck_id}", f"대여소 {self.station_id}"]
        parts += [f"실제 재고 {self.actual_bikes}대", f"싣기 {self.loaded}대", f"내리기 {self.unloaded}대"]
        if self.issue:
            parts.append(f"사정 {self.issue}")
        if self.reported_at:
            parts.append(f"보고 시각 {self.reported_at}")
        return ", ".join(parts)


@dataclass(frozen=True)
class Task:
    """One `ai.tasks` message."""

    task_id: str
    trace_id: str
    task_type: TaskType
    shift_id: str  # 근무조 ID `{권역}:{날짜}:{근무조}`
    input: str  # 기사 메모·관리자 요청·질문 (원문, Harness가 마스킹)
    brief: str = ""  # 근무조 상태 요약. 그래프 담당이 코드로 만든다
    driver_names: tuple[str, ...] = ()  # 마스킹할 기사 이름 (업무 데이터에서 전달)
    requester: Requester = Requester("manager")
    report: FieldReport | None = None  # field_report의 구조화된 값

    @property
    def thread_id(self) -> str:
        """Checkpointer thread for this task only. Tasks of the same shift never share model history, so a
        driver report cannot see or disturb a plan that is waiting for approval (PRD 6.1-4)."""
        return f"{self.shift_id}:{self.task_id}"


@dataclass
class SegmentStats:
    """Counters for one execution segment: task start or resume until interrupt or end."""

    started_at: float
    turns: int = 0
    tool_calls: int = 0
    same_tool: Counter[str] = field(default_factory=Counter)
    retries: int = 0
    fallback: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    truncated: int = 0


@dataclass
class HarnessContext:
    """LangGraph runtime context. Created fresh for every invoke/resume, so limits count per segment."""

    task: Task
    sink: EventSink
    masker: Masker
    clock: Callable[[], float] = time.monotonic
    tracer: TraceSink = field(default_factory=NullTrace)
    run_id: str | None = None
    stats: SegmentStats = field(init=False)
    rationales: dict[str, str] = field(default_factory=dict)  # tool call id -> the model's reason (trace only)
    last_model: str | None = None  # model that answered the latest call (RecoveryMiddleware)
    last_model_is_fallback: bool = False
    _attempts: dict[int, int] = field(default_factory=dict)  # turn -> model answers (validation retries add one)

    def __post_init__(self) -> None:
        self.stats = SegmentStats(started_at=self.clock())

    def elapsed(self) -> float:
        return self.clock() - self.stats.started_at

    def emit(self, type_: EventType, body: dict | None = None, harness: dict | None = None) -> None:
        """`body` holds contract fields (docs/specs TaskEvent); `harness` holds extensions not in the contract."""
        t = self.task
        self.sink.emit(
            ProgressEvent(type_, t.task_id, t.trace_id, t.shift_id, body or {}, harness or {})
        )

    def trace(self, kind: TraceKind, title: str, detail: dict | None = None, *, turn: int | None = None,
              source: TraceSource = "HARNESS") -> None:
        """One admin-log line (harness/trace.py). Masked here, so no caller can leak driver identity; never raises."""
        t = self.task
        try:
            self.tracer.trace(TraceEvent(kind, source, t.task_id, t.trace_id, t.shift_id, self.masker.text(title),
                                         self.masker.value(detail or {}), run_id=self.run_id, turn=turn))
        except Exception:  # noqa: BLE001 - the log must never change what the task does
            return

    def decision_attempt(self, turn: int) -> int:
        self._attempts[turn] = self._attempts.get(turn, 0) + 1
        return self._attempts[turn]


Status = Literal["DONE", "APPROVAL_REQUIRED", "FAILED"]


@dataclass
class Outcome:
    status: Status
    task_id: str
    trace_id: str
    run_id: str
    answer: str = ""
    code: str | None = None
    message: str | None = None
    approval: dict | None = None  # APPROVAL_REQUIRED: 계약 ApprovalRequest (승인 카드)
    rejected: bool = False
    stats: SegmentStats | None = None
