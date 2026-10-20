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
class Task:
    """One `ai.tasks` message."""

    task_id: str
    trace_id: str
    task_type: TaskType
    thread_id: str  # 근무조 ID `{권역}:{날짜}:{근무조}`; qa는 `...:qa:{task_id}`
    input: str  # 기사 메모·관리자 요청·질문 (원문, Harness가 마스킹)
    brief: str = ""  # 근무조 상태 요약. 그래프 담당이 코드로 만든다
    driver_names: tuple[str, ...] = ()  # 마스킹할 기사 이름 (업무 데이터에서 전달)


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
