"""Graph + Harness + real model (harness.yaml, Ollama Cloud) against the real business-mcp / rag-mcp.

    cd agent && set -a && . ./.env && set +a && ../.venv/bin/python <this> <business_url> <rag_url> <approve_url>
"""
import asyncio
import logging
import sys
import time

import httpx

sys.path.insert(0, ".")
logging.disable(logging.WARNING)

from graph import ShiftAgent, task_command  # noqa: E402
from graph.wiring import harness_factory, load_tools  # noqa: E402
from harness import load_config  # noqa: E402
from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

SHIFT = "gwangjin-seongdong:2026-10-21:AM"
MANAGER = {"userId": "m-1", "role": "MANAGER", "truckId": None}


async def main(business, rag, approve_url):
    config = load_config()
    tools = await load_tools({"business": business, "rag": rag}, config.observability.trace_header)
    saver = InMemorySaver()
    events = []
    shift = ShiftAgent(tools, events.append, config=config, harness=harness_factory(config, tools, saver), checkpointer=saver)

    def mine(cmd):
        return [e for e in events if e["taskId"] == cmd["taskId"]]

    for kind, text in [("QUESTION", "불꽃축제 때 대여·반납이 중지되는 대여소와 근거 문서를 알려줘"), ("PLAN_DRAFT", None)]:
        t0 = time.time()
        cmd = task_command(kind, SHIFT, user=MANAGER, text=text or "")
        out = await shift.handle(cmd)
        print(f"\n{kind}: {out['status']} {time.time() - t0:.1f}s", [e["type"] for e in mine(cmd) if e["type"] != "TOKEN"])
        if out["status"] == "APPROVAL_REQUIRED":
            card = [e for e in mine(cmd) if e["type"] == "APPROVAL_REQUIRED"][-1]["approval"]
            print("  card:", card["kind"], card["summary"], "| routes", len(card.get("routes") or []))
            httpx.post(approve_url, json={"approvalId": card["approvalId"], "role": "MANAGER", "taskId": cmd["taskId"]}).raise_for_status()
            resume = {"approvalId": card["approvalId"], "decision": "APPROVE", "decidedBy": {"userId": "m-1", "role": "MANAGER"}}
            out = await shift.handle(task_command("RESUME", SHIFT, task_id=cmd["taskId"], user=MANAGER, resume=resume))
            print(f"  after approve: {out['status']} {time.time() - t0:.1f}s",
                  [(e["type"], e.get("tool", {}).get("name"), e.get("tool", {}).get("ok")) for e in mine(cmd)[-4:] if e["type"] != "TOKEN"])
        end = mine(cmd)[-1]
        print("  end:", end["type"], (end.get("answer") or f"{end.get('code')}: {end.get('error')}")[:180].replace("\n", " "))
        print("  sources:", [(s["kind"], s["title"]) for s in end.get("sources") or []])


asyncio.run(main(*sys.argv[1:]))
