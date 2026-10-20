# Author: Cheon Seonghun
# Date: 20OCT2026
# Purpose: Runs the scenario set, scores it, and writes the before/after result table to docs/eval/.

"""Evaluation runner.

    python -m evaluation                                   # reference trajectories, no model key needed
    python -m evaluation --model openai:gpt-4.1-mini       # model scenarios on a real model (+ LLM judge)
    python -m evaluation --only SC-01,FT-05 --label prompt-v2
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from harness import FieldReport, HarnessAgent, ListSink, load_config
from harness.config import HarnessConfig
from harness.contract import TASK_TYPES, ApprovalMismatch, task_from_command
from harness.prompts import load_prompt

from .fake_mcp import DRIVER_NAMES, SHIFT_ID, connect_world
from .fakes import ScriptedChatModel
from .scenarios import APPROVE, SCENARIOS, RunRecord, Scenario
from .scorers import Check, judge_explanation, score, unapproved_changes

REPO_ROOT = Path(__file__).resolve().parents[2]


async def _no_sleep(_: float) -> None:
    return None


@dataclass
class Result:
    scenario: Scenario
    record: RunRecord
    checks: list[Check]
    model: str
    judge: int | None

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def stat(self, key: str) -> int:
        return sum(getattr(o.stats, key) for o in self.record.outcomes if o.stats)


def command_for(sc: Scenario) -> dict:
    """The `ai.tasks` TaskCommand the backend would send for this scenario (docs/specs asyncapi)."""
    stamp = time.time_ns()
    return {
        "eventId": str(uuid.uuid4()), "schemaVersion": 1, "occurredAt": "2026-10-24T13:06:00+09:00",
        "shiftId": SHIFT_ID, "traceId": f"eval-{sc.id}-{stamp}",
        "taskId": str(uuid.uuid4()), "threadId": SHIFT_ID, "type": sc.command_type,
        "text": sc.text or None, "reportId": sc.report["reportId"] if sc.report else None,
        "requestedBy": {"userId": f"eval-{sc.role.lower()}", "role": sc.role},
    }


async def run_scenario(sc: Scenario, base: HarnessConfig, model_name: str | None, judge: BaseChatModel | None) -> Result:
    config = sc.configure(base)
    world = sc.world()
    if sc.report:
        world.reports.append(sc.report)
    real = model_name is not None and sc.model_scenario
    async with connect_world(world, config.observability.trace_header) as tools:
        if real:
            kwargs = config.models.kwargs
            model: BaseChatModel = init_chat_model(model_name, **kwargs)
            fallback = init_chat_model(config.models.fallback, **kwargs) if config.models.fallback else None
        else:
            model = ScriptedChatModel(steps=list(sc.script), label="scripted")
            fallback = ScriptedChatModel(steps=list(sc.fallback_script), label="scripted-fallback")
        sink = ListSink()
        agent = HarnessAgent(
            config, tools, sink,
            model=model, fallback_model=fallback,
            project=config.observability.eval_project,
            sleep=asyncio.sleep if real else _no_sleep,
        )
        report = FieldReport.from_contract(sc.report) if sc.report else None
        task = task_from_command(command_for(sc), report=report, brief=sc.brief, driver_names=DRIVER_NAMES)
        started = time.monotonic()
        outcomes = [await agent.start(task)]
        approvals: list[dict] = []
        while outcomes[-1].status == "APPROVAL_REQUIRED" and len(approvals) < 5:
            card = outcomes[-1].approval
            template = sc.decisions[len(approvals)] if len(approvals) < len(sc.decisions) else APPROVE
            role = template.get("role", card["approver"])
            resume = {
                "approvalId": card["approvalId"],
                "decision": template["decision"],
                "excludeStationIds": template.get("excludeStationIds", []),
                "comment": template.get("comment"),
                "decidedBy": {"userId": f"eval-{role.lower()}", "role": role},
            }
            # Plays the backend: only the right approver's plain APPROVE marks the approval APPROVED.
            if resume["decision"] == "APPROVE" and role == card["approver"] and not resume["excludeStationIds"]:
                world.approve(card)
            entry = {
                "seq": world.next_seq(), "approvalId": card["approvalId"], "kind": card["kind"], "approver": card["approver"],
                "grade": card.get("grade"), "decision": resume["decision"], "role": role, "refused": False,
                "excluded": resume["excludeStationIds"],
            }
            approvals.append(entry)
            try:
                outcomes.append(await agent.resume_command(task, resume))
            except ApprovalMismatch:
                entry["refused"] = True
        record = RunRecord(sc, outcomes, sink.events, world, await agent.history(task), approvals, time.monotonic() - started)
    checks = score(record)
    graded = await judge_explanation(judge, record) if judge is not None and real else None
    return Result(sc, record, checks, model_name if real else "scripted", graded)


def _decision(a: dict) -> str:
    grade = a.get("grade")
    who = f"{a['approver']}({grade})" if grade else a["approver"]
    if a["refused"]:
        result = "거부됨"
    elif a.get("excluded"):
        result = f"제외 요청 {', '.join(a['excluded'])}"
    else:
        result = a["decision"]
    return f"{who}: {a['role']} {result}"


def render(results: list[Result], config: HarnessConfig, label: str, model_name: str | None) -> str:
    versions = sorted({load_prompt(config.prompts_dir, TASK_TYPES[r.scenario.command_type]).version for r in results})
    loop, rec, tools, ctx = config.loop, config.recovery, config.tools, config.context
    seconds = " · ".join(f"{k} {v:g}초" for k, v in loop.max_seconds.items())
    lines = [
        f"# Harness 평가 결과: {label}",
        "",
        "| 항목 | 값 |",
        "| --- | --- |",
        f"| 실행일 | {date.today().isoformat()} |",
        f"| 모델 | {model_name or 'scripted (기준 궤적, 모델 품질이 아닌 Harness·채점 경로 검증)'} |",
        f"| 대체 모델 | {config.models.fallback if model_name else 'scripted-fallback'} |",
        f"| 프롬프트 | {', '.join(versions)} |",
        f"| 한도 | {loop.max_turns}턴 · 같은 도구 {loop.max_same_tool_calls}회 · 결과 {tools.result_max_chars}자 · 토큰 {ctx.token_budget} · 재시도 {rec.model_retries}회 |",
        f"| 시간 한도 | {seconds} (시나리오별 설정 변경은 FT-06만) |",
        f"| 모델 설정 | {config.models.kwargs or '-'} |",
        "",
        "## 시나리오별 결과",
        "",
        "| ID | 시나리오 | PRD | 모델 | 판정 | 상태 | 승인 (카드 승인자: 결정자 결정) | 턴 | 도구 | 재시도 | 대체 모델 | 잘림 | 토큰(입/출) | 시간(s) | 설명 품질 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for r in results:
        final = r.record.final
        fallback = any(o.stats and o.stats.fallback for o in r.record.outcomes)
        lines.append(
            f"| {r.scenario.id} | {r.scenario.title} | {r.scenario.prd or '-'} | {r.model} | {'PASS' if r.passed else 'FAIL'} | "
            f"{final.status}{f' ({final.code})' if final.code else ''} | "
            f"{', '.join(_decision(a) for a in r.record.approvals) or '-'} | {r.stat('turns')} | {r.stat('tool_calls')} | "
            f"{r.stat('retries')} | {'예' if fallback else '-'} | {r.stat('truncated')} | "
            f"{r.stat('input_tokens')}/{r.stat('output_tokens')} | {r.record.elapsed_s:.2f} | {r.judge if r.judge is not None else '-'} |"
        )
    unapproved = sum(len(unapproved_changes(r.record)) for r in results)
    limit_codes = {"LOOP_MAX_TURNS", "LOOP_TIMEOUT", "LOOP_SAME_TOOL"}
    judged = [r.judge for r in results if r.judge is not None]
    lines += [
        "",
        "## 요약 (HARNESS.md 6.3 결과표 형식)",
        "",
        "| 지표 | 값 |",
        "| --- | --- |",
        f"| 시나리오 통과 | {sum(r.passed for r in results)}/{len(results)} |",
        f"| 무승인 변경 건수 | {unapproved} |",
        f"| 한도 초과 종료 | {sum(r.record.final.code in limit_codes for r in results)} |",
        f"| 재시도 · 대체 모델 | {sum(r.stat('retries') for r in results)} · {sum(any(o.stats and o.stats.fallback for o in r.record.outcomes) for r in results)} |",
        f"| 평균 턴 · 평균 시간(s) · 평균 토큰 | {sum(r.stat('turns') for r in results) / len(results):.1f} · "
        f"{sum(r.record.elapsed_s for r in results) / len(results):.2f} · "
        f"{sum(r.stat('input_tokens') + r.stat('output_tokens') for r in results) / len(results):.0f} |",
        f"| 설명 품질 평균 | {sum(judged) / len(judged):.1f} |" if judged else "| 설명 품질 평균 | - (실제 모델 실행 시 채점) |",
        "",
        "## 검사 상세",
        "",
    ]
    for r in results:
        lines.append(f"### {r.scenario.id} {r.scenario.title}: {'PASS' if r.passed else 'FAIL'}")
        lines.append("")
        lines += [f"- [{'통과' if c.passed else '실패'}] {c.name}: {c.detail}" for c in r.checks]
        lines.append("")
    return "\n".join(lines)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evaluation")
    parser.add_argument("--model", help="예: openai:gpt-4.1-mini. 없으면 기준 궤적(scripted)으로 실행")
    parser.add_argument("--judge", help="설명 품질 채점 모델. 기본값은 --model")
    parser.add_argument("--only", help="쉼표로 구분한 시나리오 ID")
    parser.add_argument("--label", default="baseline", help="결과 파일 이름에 붙일 변경 이름")
    parser.add_argument("--out", type=Path, help="결과 Markdown 경로. 기본값 docs/eval/YYYY-MM-DD-{label}.md")
    args = parser.parse_args(argv)

    logging.getLogger("mcp").setLevel(logging.WARNING)
    config = load_config()
    wanted = set(args.only.split(",")) if args.only else None
    scenarios = [s for s in SCENARIOS if wanted is None or s.id in wanted]
    judge_name = args.judge or args.model
    judge = init_chat_model(judge_name) if judge_name else None

    results = [await run_scenario(sc, config, args.model, judge) for sc in scenarios]
    report = render(results, config, args.label, args.model)
    out = args.out or REPO_ROOT / "docs" / "eval" / f"{date.today().isoformat()}-{args.label}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n", encoding="utf-8")
    for r in results:
        print(f"{r.scenario.id:6} {'PASS' if r.passed else 'FAIL'}  {r.record.final.status} {r.record.final.code or ''}")
    print(f"→ {out}")
    return 0 if all(r.passed for r in results) else 1
