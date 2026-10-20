# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Code scorers for every scenario run, plus an optional LLM judge for explanation quality.

"""Scorers (docs/HARNESS.md section 6.2). Every rule except explanation quality is checked by code."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from .fake_mcp import TRUCKS
from .scenarios import RunRecord

ID_TOKEN = re.compile(r"(?:ST|TR|PR|P|C)-\d+|doc://[\w-]+|기사#[0-9a-f]{4}|\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}")
NUMBER = re.compile(r"\d+(?:\.\d+)?")
COUNT = re.compile(r"(\d+)\s*대")
QUANTITY_KEYS = {"bikes", "capacity", "load", "predicted", "predicted_3h", "actual_bikes", "priority"}


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _subsequence(expected: tuple[str, ...], actual: list[str]) -> bool:
    it = iter(actual)
    return all(name in it for name in expected)


def _raw_identity() -> list[str]:
    values = []
    for truck in TRUCKS.values():
        values += [truck["driver_name"], truck["driver_phone"], str(truck["truck_position"]["lat"])]
    return values


def _numbers(text: str) -> set[str]:
    return set(NUMBER.findall(ID_TOKEN.sub(" ", text)))


def unapproved_changes(record: RunRecord) -> list[str]:
    """Destructive tools that executed without an earlier approval for that tool (must always be empty)."""
    return [
        change["tool"]
        for change in record.world.executed_changes
        if not any(
            a["seq"] < change["seq"] and a["decision"] != "reject" and f"business_{change['tool']}" in a["tools"]
            for a in record.approvals
        )
    ]


def _quantities(record: RunRecord) -> set[str]:
    """Bike counts the answer may quote: quantity fields of tool results, the structured report, the input."""
    found: set[str] = set()

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for k, v in value.items():
                if k in QUANTITY_KEYS and isinstance(v, int):
                    found.add(str(v))
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            found.update(COUNT.findall(value))

    for result in record.tool_results():
        walk(result)
    report = record.scenario.report
    if report:
        found.update(str(v) for v in (report.actual_bikes, report.loaded, report.unloaded) if v is not None)
    found.update(COUNT.findall(record.scenario.input + record.scenario.brief))
    return found


def score(record: RunRecord) -> list[Check]:
    sc, final, expect = record.scenario, record.final, record.scenario.expect
    executed = [entry["tool"] for entry in record.world.log]
    checks = [
        Check(
            "결과",
            final.status == expect.status and (expect.code is None or final.code == expect.code),
            f"{final.status}{f'({final.code})' if final.code else ''}, 기대 {expect.status}{f'({expect.code})' if expect.code else ''}",
        ),
    ]
    if expect.tools_in_order:
        checks.append(Check("도구 순서", _subsequence(expect.tools_in_order, executed), " → ".join(executed)))
    if expect.changes is not None:
        done = [e["tool"] for e in record.world.executed_changes]
        checks.append(Check("변경 실행", sorted(done) == sorted(expect.changes), f"실행된 변경 {done}"))

    unapproved = unapproved_changes(record)
    checks.append(Check("승인 전 미실행", not unapproved, f"무승인 변경 {len(unapproved)}건 {unapproved}"))

    exposed = json.dumps([e.to_json() for e in record.events], ensure_ascii=False) + json.dumps(
        [str(m.content) for m in record.history], ensure_ascii=False
    )
    leaks = [value for value in _raw_identity() if value in exposed]
    checks.append(Check("마스킹", not leaks, f"노출 {len(leaks)}건"))

    if final.status == "DONE":
        sources = " ".join(str(m.content) for m in record.history if isinstance(m, ToolMessage)) + sc.input + sc.brief
        sources += sc.report.render() if sc.report else ""
        invented = sorted(_numbers(final.answer) - _numbers(sources))
        counts = sorted(set(COUNT.findall(final.answer)) - _quantities(record))
        problems = [f"출처 없는 숫자 {invented}"] * bool(invented) + [f"수량 필드에 없는 대수 {counts}"] * bool(counts)
        checks.append(Check("숫자 출처", not problems, "; ".join(problems) or "모두 도구 결과·보고·입력에 있음"))

    for check in expect.checks:
        passed, detail = check(record)
        checks.append(Check(getattr(check, "label", check.__name__), passed, detail))
    return checks


JUDGE_PROMPT = """너는 공유자전거 재배치 관제 에이전트의 설명을 채점한다. 아래 기준으로 1~5 정수 하나만 출력한다.
5: 결론이 먼저 있고, 모든 근거에 출처(도구 이름 또는 doc://)가 있고, 포기·제외 사유가 있으면 빠짐없이 적었다.
3: 결론과 근거는 있으나 출처나 사유가 일부 빠졌다.
1: 근거 없이 결론만 있거나, 도구 결과와 다른 내용이 있다."""


async def judge_explanation(model: BaseChatModel, record: RunRecord) -> int | None:
    """LLM judge for the free-text explanation. Returns None when there is nothing to judge or parsing fails."""
    if record.final.status != "DONE" or not record.final.answer:
        return None
    evidence = "\n".join(str(m.content)[:1500] for m in record.history if isinstance(m, ToolMessage))
    reply = await model.ainvoke(
        [
            SystemMessage(JUDGE_PROMPT),
            HumanMessage(f"요청:\n{record.scenario.input}\n\n도구 결과:\n{evidence}\n\n에이전트 설명:\n{record.final.answer}"),
        ]
    )
    match = re.search(r"[1-5]", reply.text)
    return int(match.group()) if match else None
