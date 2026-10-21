# Author: Jung Minkyu
# Date: 22OCT2026
# Purpose: 추론 기록(관리자 로그) 확인. 모델의 선택 이유가 기록에만 남고 실행·승인에는 섞이지 않는지, 기록이 가려지고
#          계약(asyncapi AgentTraceMessage)에 맞는지, 기록 통로가 망가져도 작업이 그대로인지 본다.

import asyncio
import json
import logging
import unittest

from langchain_core.messages import AIMessage, SystemMessage

from evaluation.fake_mcp import DRIVER_NAMES, SHIFT_ID, TRUCKS, World, connect_world, report
from evaluation.fakes import StatusError, call, say
from graph.agentlog import TASK_ID, render, sse_events, wanted
from graph.contract import task_command
from graph.worker import TracePump
from harness import HarnessAgent, ListSink, load_config
from harness.tools import offered_tools
from harness.trace import RATIONALE
from tests.test_graph import DRIVER_TR1, MANAGER, PLAN_STEPS, contract_validator, field_report, plan_draft, shift_env
from tests.test_harness import BLOCKED, answer, blocked_task, harness, last_candidate, task

logging.getLogger("mcp").setLevel(logging.WARNING)
logging.getLogger("graph").setLevel(logging.CRITICAL)

CONFIG = load_config()
TRACE_SCHEMA = contract_validator("asyncapi.yaml#/components/schemas/AgentTraceMessage")
ALL_TOOLS = set(CONFIG.tools.allow["plan_draft"])
READ_TOOLS = set(CONFIG.tools.allow["qa"])


def chose(name, why, **args):
    """모델 한 턴: 도구 하나와 그 선택 이유(rationale)."""
    return AIMessage(content="", tool_calls=[{"name": name, "args": {RATIONALE: why, **args}, "id": "x"}])


def plan_with_reasons():
    return [
        chose("business_get_risk_stations", "계획 규모를 보려고 위험 대여소부터 본다", region_id="yeongdeungpo"),
        chose("business_request_route_plan", "위험 대여소가 여럿이라 근무조 경로를 계산한다",
              shift_id=SHIFT_ID, kind="PLAN", truck_ids=[], exclude_station_ids=[]),
        lambda m: chose("business_submit_plan", "계산한 계획을 관리자 승인으로 올린다",
                        plan_id=last_candidate(m)["plan"]["planId"]),
        say("계획을 활성화했다."),
    ]


def kinds(traces):
    return [t.kind for t in traces]


class RationaleTest(unittest.IsolatedAsyncioTestCase):
    async def test_rationale_is_asked_for_and_never_reaches_tools_cards_or_history(self):
        async with harness(plan_with_reasons()) as (agent, _, _, world, sink, _):
            for tool in agent.tools:
                self.assertEqual(next(iter(tool.args_schema["properties"])), RATIONALE, tool.name)  # 이유를 먼저 쓴다
                self.assertIn(RATIONALE, tool.args_schema["required"])
            t = task()
            out = await agent.start(t)
            self.assertNotIn(RATIONALE, json.dumps(out.approval, ensure_ascii=False))
            done = await answer(agent, world, t, out)
            self.assertEqual(done.status, "DONE")
            self.assertTrue(all(RATIONALE not in entry["args"] for entry in world.log))  # MCP·backend가 받은 인자
            self.assertEqual(set(world.executed_changes[0]["args"]), {"plan_id", "approval_id", "task_id"})
            stored = [tc["args"] for m in await agent.history(t) if isinstance(m, AIMessage) for tc in m.tool_calls]
            self.assertTrue(stored and all(RATIONALE not in a for a in stored))  # 체크포인트·다음 턴 입력
            reasons = [c["rationale"] for e in sink.traces if e.kind == "DECISION" for c in e.detail["calls"]]
            self.assertEqual(reasons, ["계획 규모를 보려고 위험 대여소부터 본다", "위험 대여소가 여럿이라 근무조 경로를 계산한다",
                                       "계산한 계획을 관리자 승인으로 올린다"])
            calls = [e for e in sink.traces if e.kind == "TOOL_CALL"]
            self.assertEqual(calls[0].detail["rationale"], "계획 규모를 보려고 위험 대여소부터 본다")

    async def test_same_call_limit_ignores_a_changing_rationale(self):
        steps = [chose("business_get_station_detail", f"다시 본다 {i}", station_id="ST-2001") for i in range(5)]
        async with harness(steps) as (agent, _, _, world, sink, _):
            out = await agent.start(task("qa", "ST-2001 상태"))
            self.assertEqual(out.code, "LOOP_SAME_TOOL")
            self.assertEqual(len([e for e in world.log if e["tool"] == "get_station_detail"]), 3)
            guards = [e.detail["action"] for e in sink.traces if e.kind == "GUARD"]
            self.assertEqual(guards, ["BLOCK", "FAIL"])

    async def test_arguments_are_still_validated_after_the_rationale_is_removed(self):
        forged = chose("business_submit_plan", "승인 ID를 직접 넣는다", plan_id="P-1", approval_id="forged")
        async with harness([forged, forged]) as (agent, _, _, world, sink, _):
            out = await agent.start(task())
            self.assertEqual(out.code, "OUTPUT_INVALID")
            self.assertEqual(world.executed_changes, [])
            guard = [e for e in sink.traces if e.kind == "GUARD"]
            self.assertEqual([g.detail["action"] for g in guard], ["RETRY", "FAIL"])
            self.assertTrue(any("approval_id" in r for r in guard[0].detail["reasons"]))
            self.assertFalse(any(RATIONALE in r for g in guard for r in g.detail["reasons"]))


class TraceSequenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_segments_record_choices_tools_approval_and_end_in_order(self):
        async with harness(plan_with_reasons()) as (agent, _, _, world, sink, _):
            t = task()
            out = await agent.start(t)
            first = list(sink.traces)
            self.assertEqual(kinds(first), [
                "RUN_START", "DECISION", "TOOL_CALL", "TOOL_RESULT", "DECISION", "TOOL_CALL", "TOOL_RESULT", "DECISION",
                "APPROVAL_WAIT", "RUN_END",
            ])
            start = first[0].detail
            self.assertEqual((start["segment"], start["taskType"]), ("start", "plan_draft"))
            self.assertEqual(set(start["tools"]), ALL_TOOLS)  # 구현된 도구 12개를 모두 고를 수 있다
            self.assertEqual(start["withheld"], [])
            self.assertEqual([e.turn for e in first if e.kind == "DECISION"], [1, 2, 3])
            self.assertEqual(first[-2].detail["approvalId"], out.approval["approvalId"])
            self.assertEqual(first[-1].detail["status"], "APPROVAL_REQUIRED")

            await answer(agent, world, t, out)
            second = sink.traces[len(first):]
            self.assertEqual(kinds(second), ["RUN_START", "TOOL_CALL", "TOOL_RESULT", "DECISION", "RUN_END"])
            self.assertEqual(second[0].detail["segment"], "resume")
            self.assertEqual(second[1].detail["injected"], ["approval_id", "task_id"])  # Harness가 채운 인자
            self.assertEqual(second[3].detail["answer"], "계획을 활성화했다.")
            self.assertEqual(second[-1].detail["status"], "DONE")
            self.assertEqual({e.run_id for e in first} & {e.run_id for e in second}, set())  # 구간마다 run id

    async def test_retries_and_fallback_are_recorded(self):
        steps = [StatusError(429)] * 3
        async with harness(steps, fallback_steps=[say("대체 모델 답")]) as (agent, _, _, _, sink, _):
            out = await agent.start(task("qa", "위험 대여소"))
            self.assertEqual(out.status, "DONE")
            recovery = [(e.detail["action"], e.detail.get("status")) for e in sink.traces if e.kind == "RECOVERY"]
            self.assertEqual(recovery, [("RETRY", 429), ("RETRY", 429), ("FALLBACK", None)])
            decision = next(e for e in sink.traces if e.kind == "DECISION")
            self.assertTrue(decision.detail["fallback"])
            self.assertEqual(decision.detail["model"], "fallback")

    async def test_tools_offered_and_withheld_with_reasons(self):
        async with connect_world(World()) as tools:
            offered, withheld = offered_tools(tools, "manager_request", "manager", CONFIG.tools)
            self.assertEqual({t.name for t in offered}, ALL_TOOLS)
            self.assertEqual(withheld, [])
            offered, withheld = offered_tools(tools, "field_report", "driver", CONFIG.tools)
            self.assertEqual({t.name for t in offered}, ALL_TOOLS - {"business_get_audit", "business_submit_plan"})
            self.assertEqual({w["name"] for w in withheld}, {"business_get_audit", "business_submit_plan"})
            self.assertTrue(all("NFR-06" in w["why"] for w in withheld))
            offered, withheld = offered_tools(tools, "qa", "manager", CONFIG.tools)
            self.assertEqual({t.name for t in offered}, READ_TOOLS)  # 질의는 읽기 도구 8개 전부
            self.assertEqual(len(withheld), 4)


class TraceSafetyTest(unittest.IsolatedAsyncioTestCase):
    async def test_traces_never_carry_driver_identity(self):
        memo = "김민수입니다. 010-2345-6789로 연락 주세요."
        world = World(reports=[report("R-1", "TR-1", "ST-2003", 3, "OTHER", memo)])
        steps = [chose("business_get_truck_status", "적재량을 본다", shift_id=SHIFT_ID),
                 chose("business_get_field_reports", "보고를 본다", shift_id=SHIFT_ID), say("확인")]
        raw = [*DRIVER_NAMES[:1], "010-2345-6789", str(TRUCKS["TR-1"]["lat"]), str(TRUCKS["TR-1"]["lon"])]
        async with harness(steps, world=world) as (agent, _, _, _, sink, _):
            await agent.start(task("qa", f"{memo} 트럭 상태", brief="TR-1 기사 김민수", driver_names=DRIVER_NAMES))
            logged = json.dumps([e.to_json() for e in sink.traces], ensure_ascii=False)
            for value in raw:
                self.assertNotIn(value, logged)
            self.assertIn("[위치]", logged)  # 트럭 좌표는 결과 미리보기에서도 가려진다

    async def test_a_broken_trace_channel_changes_nothing(self):
        class Broken:
            def trace(self, event):
                raise RuntimeError("ai.traces down")

        results = []
        for tracer in (None, Broken()):
            world = World()
            async with connect_world(world) as tools:
                from evaluation.fakes import ScriptedChatModel
                model = ScriptedChatModel(steps=plan_with_reasons())
                sink = ListSink()
                agent = HarnessAgent(CONFIG, tools, sink, model=model, fallback_model=model, tracer=tracer)
                t = task()
                out = await agent.start(t)
                done = await answer(agent, world, t, out)
                results.append((out.status, out.approval["kind"], done.status, [c["tool"] for c in world.executed_changes],
                                [e.type for e in sink.events]))
        self.assertEqual(results[0], results[1])

    async def test_first_message_names_the_shift_and_region(self):
        async with harness([say("답")]) as (agent, model, _, _, _, _):
            await agent.start(task("qa", "질문"))
            self.assertIn(f"근무조: {SHIFT_ID} (구역 yeongdeungpo)", model.seen[0][-1].content)

    async def test_report_time_is_given_to_the_model(self):
        async with harness([say("확인")], world=World(reports=[BLOCKED])) as (agent, model, _, _, _, _):
            await agent.start(blocked_task())
            self.assertIn("보고 시각", model.seen[0][-1].content)

    async def test_model_is_told_once_that_the_approved_change_ran(self):
        async with harness(plan_with_reasons()) as (agent, model, _, world, _, _):
            t = task()
            out = await agent.start(t)
            await answer(agent, world, t, out)
            systems = [next(m.content for m in seen if isinstance(m, SystemMessage)) for seen in model.seen]
            self.assertEqual(["방금 일어난 일" in s for s in systems], [False, False, False, True])
            self.assertIn("business_submit_plan", systems[-1])


class GraphTraceTest(unittest.IsolatedAsyncioTestCase):
    async def test_route_reasons_and_rule_path_decisions(self):
        traces = []
        async with shift_env(world=World(reports=[BLOCKED])) as env:
            env.shift.sink._trace = traces.append
            cmd = field_report()  # 메모 없는 보고: 규칙 경로
            await env.shift.handle(cmd)
            await env.shift.handle(cmd)  # 같은 작업이 다시 왔다
            routes = [t for t in traces if t["kind"] == "ROUTE"]
            self.assertEqual([r["detail"]["path"] for r in routes], ["code", "skip"])
            self.assertIn("메모 없는 현장 보고", routes[0]["detail"]["why"])
            self.assertIn("이미 받은 작업", routes[1]["detail"]["why"])
            rule = next(t for t in traces if t["kind"] == "DECISION")
            self.assertEqual(rule["source"], "CODE")
            self.assertIn("ST-2005를 빼고 TR-1", rule["title"])
            self.assertEqual(rule["detail"]["calls"][0]["name"], "business_request_route_plan")
            self.assertIn("APPROVAL_WAIT", [t["kind"] for t in traces])
            self.assertEqual({t["source"] for t in traces}, {"GRAPH", "CODE"})

    async def test_every_trace_matches_the_contract(self):
        traces = []
        # 주 모델과 대체 모델(같은 스크립트 모델)이 재시도까지 모두 503이면 규칙 경로로 다시 한다
        steps = [*PLAN_STEPS, say("완료"), *[StatusError(503)] * 6]
        async with shift_env(steps, world=World(reports=[BLOCKED])) as env:
            env.shift.sink._trace = traces.append
            cmd = plan_draft()
            await env.shift.handle(cmd)
            await env.decide(cmd)
            await env.shift.handle(field_report())  # 규칙 경로
            await env.shift.handle(plan_draft())  # 모델 장애 → 규칙 경로로 다시
            await env.shift.handle(task_command("QUESTION", SHIFT_ID, user=DRIVER_TR1, text="내 다음 정류장?"))
        self.assertTrue(traces)
        for t in traces:
            self.assertEqual([e.message for e in TRACE_SCHEMA.iter_errors(t)], [], t["kind"])
        self.assertEqual({t["source"] for t in traces}, {"GRAPH", "HARNESS", "CODE"})
        fallback = [t for t in traces if t["kind"] == "ROUTE" and t["detail"].get("fallback")]
        self.assertEqual(len(fallback), 1)

    def test_rule_path_previews_are_masked_before_they_are_cut(self):
        """잘린 JSON은 키(lat·lon)로 가릴 수 없다. 규칙 경로는 가린 뒤에 자른다."""
        from graph.codepath import CodePath

        code = CodePath([], CONFIG.with_tools())
        trucks = [{**TRUCKS["TR-1"], "note": "x" * 3000}]
        preview = code._preview(json.dumps(trucks, ensure_ascii=False))
        self.assertIn("[위치]", preview)
        self.assertNotIn(str(TRUCKS["TR-1"]["lat"]), preview)
        self.assertLessEqual(len(preview), CONFIG.observability.trace_preview_chars + 20)

    async def test_a_failing_trace_publisher_never_blocks_progress_events(self):
        def broken(_):
            raise ConnectionError("broker down")

        async with shift_env([*PLAN_STEPS, say("완료")]) as env:
            env.shift.sink._trace = broken
            cmd = plan_draft()
            self.assertEqual((await env.shift.handle(cmd))["status"], "APPROVAL_REQUIRED")
            self.assertEqual((await env.decide(cmd))["status"], "DONE")
            self.assertEqual([c[0] for c in env.changes()], ["submit_plan"])
            self.assertEqual(env.of("DONE")[-1]["answer"], "완료")


class TracePumpTest(unittest.IsolatedAsyncioTestCase):
    async def test_trace_pump_drops_instead_of_retrying_or_blocking(self):
        attempts = []

        async def send(topic, key, value, headers):
            attempts.append(topic)
            await asyncio.sleep(0)  # 실제 발행기처럼 한 번 양보한다 (다시 보내기를 되풀이하면 아래 한도에 걸린다)
            raise ConnectionError("down")

        pump = TracePump(send, max_queue=2, timeout=0.1)
        for i in range(4):
            pump.publish({"kind": "DECISION", "taskId": str(i), "shiftId": SHIFT_ID})
        self.assertEqual(pump.dropped, 2)  # 대기열이 차면 바로 버린다 (publish는 막지 않는다)
        pump.start()
        await asyncio.wait_for(pump.queue.join(), 1)
        self.assertEqual(attempts, ["ai.traces", "ai.traces"])  # 다시 보내지 않는다
        self.assertEqual(pump.dropped, 4)
        await asyncio.wait_for(pump.stop(timeout=0.1), 1)


class CliTest(unittest.TestCase):
    def test_cli_shows_each_choice_with_its_reason(self):
        event = {"occurredAt": "2026-10-22T07:11:03+09:00", "taskId": "5ae7684c-1111", "source": "HARNESS", "turn": 2,
                 "kind": "DECISION", "title": "선택: business_propose_route_change(candidate_id=C-1)",
                 "detail": {"calls": [{"name": "business_propose_route_change", "rationale": "MINOR라 기사 수락을 요청한다"}]}}
        lines = render(event)
        self.assertIn("판단", lines[0])
        self.assertIn("턴2", lines[0])
        self.assertTrue(lines[1].endswith("└ business_propose_route_change: MINOR라 기사 수락을 요청한다"))
        parsed = list(sse_events([": ping", "", "id: 42", "event: DECISION", f"data: {json.dumps(event)}", ""]))
        self.assertEqual(parsed, [("42", event)])

    def test_cli_sends_only_full_task_ids_to_the_server_and_filters_prefixes_itself(self):
        full = "5ae7684c-1111-4ab7-a636-94656f049298"
        self.assertIsNotNone(TASK_ID.fullmatch(full))
        self.assertIsNone(TASK_ID.fullmatch("5ae7684c"))  # 서버는 UUID만 받는다 (400)
        self.assertTrue(wanted({"taskId": full}, "5ae7684c", None, None))
        self.assertFalse(wanted({"taskId": full}, "9b0c", None, None))
