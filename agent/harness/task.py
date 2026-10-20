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

from .events import EventSink
from .guardrails import Masker

TaskType = Literal["plan_draft", "field_report", "manager_request", "qa"]


@dataclass(frozen=True)
class Requester:
    """Who created the task (from the backend's authenticated user, never from model output)."""

    role: Literal["manager", "driver", "scheduler"]
    truck_id: str | None = None  # 기사면 필수: 기사는 자기 트럭만 조회·제안한다 (NFR-06)

    def __post_init__(self) -> None:
        if self.role == "driver" and not self.truck_id:
            raise ValueError("driver requester needs truck_id")


@dataclass(frozen=True)
class FieldReport:
    """Structured part of a driver report (PRD 6.5 `field_report`). Numbers here come from the driver app,
    so they are the only bike counts a model may put into constraints (NFR-07)."""

    truck_id: str
    station_id: str
    actual_bikes: int | None = None
    loaded: int = 0
    unloaded: int = 0
    issue: Literal["blocked", "construction", "other"] | None = None

    def render(self) -> str:
        parts = [f"트럭 {self.truck_id}", f"대여소 {self.station_id}"]
        if self.actual_bikes is not None:
            parts.append(f"실제 재고 {self.actual_bikes}대")
        parts += [f"싣기 {self.loaded}대", f"내리기 {self.unloaded}대"]
        if self.issue:
            parts.append(f"사정 {self.issue}")
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
    stats: SegmentStats = field(init=False)

    def __post_init__(self) -> None:
        self.stats = SegmentStats(started_at=self.clock())

    def elapsed(self) -> float:
        return self.clock() - self.stats.started_at


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
    approvals: list[dict] = field(default_factory=list)  # APPROVAL_REQUIRED일 때 승인할 도구 호출
    rejected: bool = False
    stats: SegmentStats | None = None
