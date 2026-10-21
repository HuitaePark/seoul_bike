# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: ai.tasks(TaskCommand)를 검사하고 읽는다. 카드·이벤트 모양은 Harness(harness.contract, ProgressEvent)가 맡는다.

"""메시지 계약 (docs/specs asyncapi `TaskCommand`).

asyncapi v0.3: `threadId`는 작업 단위 `{shiftId}:{taskId}`(Harness 작업 스레드), `requestedBy.truckId`는 필수(기사가 아니면 null).
Harness v0.5부터 TaskCommand → Task 변환(`task_from_command`), RESUME → 결정(`resume_command`), 승인 카드,
`TaskEventMessage` 이벤트는 Harness가 만든다 (HARNESS 1.4·1.5). 그래프는 그 앞에서 명령을 검사해 계약 위반을 DLQ로
보내고, 현장 보고 값을 Harness `FieldReport` 모양으로 읽는다. 명세 확장 제안(AGENT-GRAPH.md 7절 G2)은 있으면 쓴다.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

SCHEMA_VERSION = 1
TASK_TYPES = {"PLAN_DRAFT", "FIELD_REPORT", "REQUEST", "QUESTION"}
COMMAND_TYPES = {*TASK_TYPES, "RESUME"}

# 일부 모델이 ID에 쓰는 유니코드 하이픈 (U+2010, U+2011). 이벤트로 내보내기 전에 ASCII로 바꾼다.
HYPHENS = str.maketrans({"‐": "-", "‑": "-"})


class CommandError(ValueError):
    """계약에 맞지 않는 명령. 다시 보내도 결과가 같으므로 재시도하지 않고 DLQ로 보낸다."""


def base_name(tool: str) -> str:
    """`business_get_risk_stations` → `get_risk_stations`. MCP 서버 접두어를 뗀다."""
    for prefix in ("business_", "rag_"):
        if tool.startswith(prefix):
            return tool[len(prefix):]
    return tool


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def plain_hyphens(value: Any) -> Any:
    """U+2010·U+2011 하이픈을 ASCII `-`로. 일부 모델(gpt-oss)이 ID를 `ST‑2001`처럼 써서 화면 링크·ID 비교가 깨진다."""
    if isinstance(value, str):
        return value.translate(HYPHENS)
    if isinstance(value, dict):
        return {k: plain_hyphens(v) for k, v in value.items()}
    if isinstance(value, list):
        return [plain_hyphens(v) for v in value]
    return value


def _pick(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if d.get(k) is not None:
            return d[k]
    return default


def report_contract(raw: dict, report_id: str | None = None) -> dict | None:
    """현장 보고 → internal-api `FieldReport` 모양(camelCase). 명령의 확장 필드(G2)와 `get_field_reports` 결과 둘 다 받는다.

    `actualBikes`가 없으면 Harness `FieldReport`를 만들 수 없으므로 None (보고를 못 읽은 것으로 본다).
    """
    actual = _pick(raw, "actualBikes", "actual_bikes")
    if actual is None:
        return None
    issue = _pick(raw, "issue")
    return {
        "reportId": _pick(raw, "reportId", "report_id", default=report_id),
        "truckId": _pick(raw, "truckId", "truck_id"),
        "stationId": _pick(raw, "stationId", "station_id"),
        "actualBikes": int(actual),
        "loaded": int(_pick(raw, "loaded", default=0)),
        "unloaded": int(_pick(raw, "unloaded", default=0)),
        "issue": str(issue).upper() if issue else None,
        "memo": (_pick(raw, "memo", default="") or "").strip() or None,
    }


@dataclass(frozen=True)
class Resume:
    """RESUME 명령의 승인 결정 (backend가 승인 기록을 남긴 뒤 보낸다)."""

    approval_id: str
    decision: str  # APPROVE | REJECT
    exclude_station_ids: tuple[str, ...] = ()
    comment: str = ""
    decided_by: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TaskCommand:
    """`ai.tasks` 메시지 1건."""

    event_id: str
    shift_id: str
    trace_id: str
    task_id: str
    type: str
    text: str = ""
    report_id: str | None = None
    report: dict | None = None  # 확장 G2: 보고 값을 명령에 함께 실으면 도구로 다시 읽지 않는다
    resume: Resume | None = None
    requested_by: dict = field(default_factory=dict)  # {userId, role, truckId (기사가 아니면 None)}

    @classmethod
    def parse(cls, raw: Any) -> TaskCommand:
        """JSON 객체를 검사해 명령으로 만든다. 계약 위반이면 CommandError."""
        if not isinstance(raw, dict):
            raise CommandError("명령은 JSON 객체여야 한다")
        required = ("eventId", "schemaVersion", "shiftId", "taskId", "threadId", "type", "requestedBy")
        missing = [k for k in required if raw.get(k) in (None, "")]
        if missing:
            raise CommandError(f"필수 필드 없음: {', '.join(missing)}")
        if raw["schemaVersion"] != SCHEMA_VERSION:
            raise CommandError(f"모르는 schemaVersion: {raw['schemaVersion']}")
        if raw["type"] not in COMMAND_TYPES:
            raise CommandError(f"모르는 type: {raw['type']}")
        if raw["threadId"] != f"{raw['shiftId']}:{raw['taskId']}":
            # asyncapi v0.3: threadId는 작업 단위 `{shiftId}:{taskId}`이고 backend가 정한다. Harness 작업 스레드가 이 값이다
            raise CommandError("threadId가 {shiftId}:{taskId}가 아니다")
        by = raw["requestedBy"]
        if not isinstance(by, dict) or not by.get("userId") or by.get("role") not in {"MANAGER", "DRIVER", "EXECUTIVE"}:
            raise CommandError("requestedBy에는 userId와 role(MANAGER|DRIVER|EXECUTIVE)이 필요하다")
        if "truckId" not in by:
            # asyncapi v0.3: 기사면 그 트럭, 아니면 null. 기사 작업을 그 트럭으로 한정한다 (NFR-06)
            raise CommandError("requestedBy에는 truckId(기사가 아니면 null)가 필요하다")

        resume = None
        if raw["type"] == "RESUME":
            r = raw.get("resume")
            if not isinstance(r, dict) or not r.get("approvalId") or r.get("decision") not in {"APPROVE", "REJECT"}:
                raise CommandError("RESUME에는 resume.approvalId와 decision(APPROVE|REJECT)이 필요하다")
            if not isinstance(r.get("decidedBy"), dict):
                raise CommandError("RESUME에는 resume.decidedBy가 필요하다")
            resume = Resume(
                approval_id=r["approvalId"],
                decision=r["decision"],
                exclude_station_ids=tuple(r.get("excludeStationIds") or ()),
                comment=(r.get("comment") or "").strip(),
                decided_by=dict(r["decidedBy"]),
            )
        if raw["type"] == "FIELD_REPORT" and not raw.get("reportId"):
            raise CommandError("FIELD_REPORT에는 reportId가 필요하다")
        if raw["type"] in {"REQUEST", "QUESTION"} and not (raw.get("text") or "").strip():
            raise CommandError(f"{raw['type']}에는 text가 필요하다")

        report = raw.get("report")
        return cls(
            event_id=raw["eventId"],
            shift_id=raw["shiftId"],
            trace_id=raw.get("traceId") or raw["taskId"],
            task_id=raw["taskId"],
            type=raw["type"],
            text=(raw.get("text") or "").strip(),
            report_id=raw.get("reportId"),
            report=report_contract(report, raw.get("reportId")) if isinstance(report, dict) else None,
            resume=resume,
            requested_by=dict(by),
        )


def task_command(
    type_: str,
    shift_id: str,
    *,
    task_id: str | None = None,
    user: dict | None = None,
    text: str = "",
    report_id: str | None = None,
    report: dict | None = None,
    resume: dict | None = None,
    trace_id: str | None = None,
) -> dict:
    """TaskCommand JSON을 만든다. backend(TaskCommandPublisher)가 하는 일이지만 테스트가 같은 모양을 쓰도록 여기 둔다."""
    task_id = task_id or str(uuid.uuid4())
    body: dict[str, Any] = {
        "eventId": str(uuid.uuid4()),
        "schemaVersion": SCHEMA_VERSION,
        "occurredAt": now_iso(),
        "shiftId": shift_id,
        "traceId": trace_id or uuid.uuid4().hex,
        "taskId": task_id,
        "threadId": f"{shift_id}:{task_id}",
        "type": type_,
        "requestedBy": {"truckId": None, **(user or {"userId": "manager-1", "role": "MANAGER"})},
    }
    if text:
        body["text"] = text
    if report_id:
        body["reportId"] = report_id
    if report:
        body["report"] = report
    if resume:
        body["resume"] = resume
    return body
