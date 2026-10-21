# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 프로세스를 나눠 PostgreSQL 체크포인터 재개를 확인한다. 1번 프로세스가 승인 대기에서 끝나고, 2번 프로세스가 승인을 받아 이어서 실행한다.

"""PostgreSQL 재개 테스트. `AGENT_TEST_PG_URL`이 있을 때만 돈다.

    AGENT_TEST_PG_URL='postgresql://minkyu@127.0.0.1:5432/ai_capstone_agent' ../.venv/bin/python -m unittest tests.test_graph_postgres
"""
import json
import os
import subprocess
import sys
import unittest
import uuid
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
PG_URL = os.environ.get("AGENT_TEST_PG_URL")

PHASE = r'''
import asyncio, json, logging, sys
logging.disable(logging.WARNING)
import json
from langchain_core.messages import ToolMessage
from evaluation.fake_mcp import World, connect_world
from evaluation.fakes import ScriptedChatModel, call, say
from graph import ShiftAgent, task_command
from graph.checkpoint import open_checkpointer
from harness import HarnessAgent, load_config

def submit_last(messages):
    cand = next(json.loads(m.content) for m in reversed(messages) if isinstance(m, ToolMessage) and "candidateId" in str(m.content))
    return call("business_submit_plan", plan_id=cand["plan"]["planId"])

async def main(phase, pg, shift_id, info):
    world = World()
    async with connect_world(world) as tools, open_checkpointer(pg) as cp:
        if phase == "start":
            steps = [call("business_request_route_plan", shift_id=shift_id, kind="PLAN", truck_ids=[], exclude_station_ids=[]),
                     submit_last]
        else:
            steps = [say("계획 활성화 완료")]
        model = ScriptedChatModel(steps=steps)
        events = []
        shift = ShiftAgent(tools, events.append, checkpointer=cp, harness=lambda sink: HarnessAgent(
            load_config(), tools, sink, model=model, fallback_model=model, checkpointer=cp))
        card = info and json.loads(info).get("card")
        if phase == "start":
            cmd = task_command("PLAN_DRAFT", shift_id)
            out = await shift.handle(cmd)
            print(json.dumps({"taskId": cmd["taskId"], "approvalId": out["approvalId"], "last": events[-1]["type"],
                              "card": events[-1]["approval"]}))
        else:
            info = json.loads(info)
            world.candidate("PLAN", [], [])  # backend는 후보를 저장해 둔다. 가짜 World는 프로세스마다 새로 만든다 (같은 값)
            world.approve(card)  # backend가 승인을 기록했다 (변경 도구가 approval_id를 다시 확인한다)
            resume = {"approvalId": info["approvalId"], "decision": "APPROVE",
                      "decidedBy": {"userId": "manager-1", "role": "MANAGER"}}
            out = await shift.handle(task_command("RESUME", shift_id, task_id=info["taskId"], resume=resume))
            state = await shift.state(shift_id)
            print(json.dumps({"status": out["status"], "types": [e["type"] for e in events],
                              "tasks": sorted({e["taskId"] for e in events}),
                              "changes": [c["tool"] for c in world.executed_changes], "plan": state["plan"]}))

asyncio.run(main(*sys.argv[1:5]))
'''


def run_phase(phase: str, shift_id: str, info: str = "") -> dict:
    proc = subprocess.run(
        [sys.executable, "-c", PHASE, phase, PG_URL, shift_id, info],
        cwd=AGENT_DIR, capture_output=True, text=True, timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr[-2000:])
    return json.loads(proc.stdout.strip().splitlines()[-1])


@unittest.skipUnless(PG_URL, "AGENT_TEST_PG_URL이 없으면 건너뛴다")
class PostgresResumeTest(unittest.TestCase):
    def test_approval_resumes_in_new_process(self):
        shift_id = f"pgtest{uuid.uuid4().hex[:8]}:2026-10-24:PM"
        first = run_phase("start", shift_id)
        second = run_phase("resume", shift_id, json.dumps(first))
        self.assertEqual(second["status"], "DONE")
        self.assertEqual(second["changes"], ["submit_plan"])  # 1번 프로세스의 승인 대기 호출이 2번에서 실행됐다
        self.assertEqual(first["last"], "APPROVAL_REQUIRED")
        self.assertEqual(second["tasks"], [first["taskId"]])  # 재개한 이벤트도 같은 작업으로 간다
        self.assertEqual(second["types"][-1], "DONE")
        self.assertEqual(second["plan"]["active"], first["card"]["planId"])


if __name__ == "__main__":
    unittest.main()
