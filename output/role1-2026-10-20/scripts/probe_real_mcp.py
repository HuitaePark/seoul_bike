"""Graph (model off) against the real business-mcp / rag-mcp over Streamable HTTP.

    cd agent && ../.venv/bin/python <this> <business_url> <rag_url> [approve_url]

approve_url: the test wrapper's /_test/approve (stands in for the backend's approval record). Without it the change
tools must be refused (403 APPROVAL_REQUIRED) and the graph must report FAILED, not success.
"""
import asyncio
import json
import logging
import sys

import httpx

sys.path.insert(0, ".")
logging.disable(logging.WARNING)

from graph import ShiftAgent, task_command  # noqa: E402
from graph.wiring import load_tools  # noqa: E402
from harness import load_config  # noqa: E402

SHIFT = "gwangjin-seongdong:2026-10-21:AM"
MANAGER = {"userId": "m-1", "role": "MANAGER", "truckId": None}
DRIVER = {"userId": "d-1", "role": "DRIVER", "truckId": "TR-01"}


async def main(business, rag, approve_url=None):
    config = load_config()
    tools = await load_tools({"business": business, "rag": rag}, config.observability.trace_header)
    names = sorted(t.name for t in tools)
    print("tools:", len(names), names)
    change = {t.name: sorted((t.args or {}).keys()) for t in tools if t.name in
              ("business_submit_plan", "business_propose_route_change", "business_add_constraint")}
    print("change tool args:", change)

    events = []
    shift = ShiftAgent(tools, events.append, config=config)  # 모델 없음 → LLM 없는 경로

    async def decide(cmd, card, user):
        if approve_url:
            httpx.post(approve_url, json={"approvalId": card["approvalId"], "role": card["approver"],
                                          "taskId": cmd["taskId"], "candidateId": card.get("candidateId")}).raise_for_status()
        body = {"approvalId": card["approvalId"], "decision": "APPROVE",
                "decidedBy": {"userId": user["userId"], "role": user["role"]}}
        return await shift.handle(task_command("RESUME", SHIFT, task_id=cmd["taskId"], user=user, resume=body))

    def last(type_, task_id):
        return [e for e in events if e["type"] == type_ and e["taskId"] == task_id][-1]

    # 03a 계획 초안
    cmd = task_command("PLAN_DRAFT", SHIFT, user=MANAGER)
    out = await shift.handle(cmd)
    print("\nPLAN_DRAFT:", out["status"])
    if out["status"] == "APPROVAL_REQUIRED":
        card = last("APPROVAL_REQUIRED", cmd["taskId"])["approval"]
        print("  card:", card["kind"], card["approver"], card["summary"], "| routes:", [r["truckId"] for r in card.get("routes", [])])
        print("  reasons:", card["reasons"][:4])
        out = await decide(cmd, card, {"userId": "m-1", "role": "MANAGER", "truckId": None})
        end = [e for e in events if e["taskId"] == cmd["taskId"]][-1]
        print("  after approve:", out["status"], "|", end.get("answer") or f"{end.get('code')}: {end.get('error')}")
    else:
        print("  ", [e for e in events if e["taskId"] == cmd["taskId"]][-1])

    # 03b 현장 보고 (backend가 G13 뒤 보낼 모양: reportId만, 보고 값은 get_field_reports로 읽는다)
    cmd = task_command("FIELD_REPORT", SHIFT, user=DRIVER, report_id="FR-881")
    out = await shift.handle(cmd)
    print("\nFIELD_REPORT FR-881:", out["status"], out.get("reason") or "")
    for _ in range(3):
        if out["status"] != "APPROVAL_REQUIRED":
            break
        card = last("APPROVAL_REQUIRED", cmd["taskId"])["approval"]
        print("  card:", card["summary"], "| grade", card.get("grade"), "| required", card.get("requiredApprovers"),
              "| candidate", card.get("candidateId"))
        user = DRIVER if card["approver"] == "DRIVER" else MANAGER
        out = await decide(cmd, card, user)
    end = [e for e in events if e["taskId"] == cmd["taskId"]][-1]
    print("  end:", out["status"], "|", (end.get("answer") or f"{end.get('code')}: {end.get('error')}")[:160])

    # 03b 진입 불가 보고 (확장 G2: 보고 값을 명령에 싣는다). TR-01 현재 경로의 첫 정류장
    plan_tool = next(t for t in tools if t.name == "business_get_current_plan")
    res = await plan_tool.ainvoke({"type": "tool_call", "name": plan_tool.name, "args": {"shift_id": SHIFT}, "id": "p"})
    current = json.loads(res.content if isinstance(res.content, str) else res.content[0]["text"])
    stop = next(s["stationId"] for s in next(r for r in current["routes"] if r["truckId"] == "TR-01")["stops"]
                if s.get("status") != "DONE")  # 이미 다녀간 정류장은 빼지 않는다
    values = {"reportId": "FR-E2E", "truckId": "TR-01", "stationId": stop, "actualBikes": 0, "issue": "BLOCKED", "memo": None}
    cmd = task_command("FIELD_REPORT", SHIFT, user=DRIVER, report_id="FR-E2E", report=values)
    out = await shift.handle(cmd)
    print(f"\nFIELD_REPORT BLOCKED {stop}:", out["status"], out.get("reason") or "")
    for _ in range(3):
        if out["status"] != "APPROVAL_REQUIRED":
            break
        card = last("APPROVAL_REQUIRED", cmd["taskId"])["approval"]
        print("  card:", card["summary"], "| grade", card.get("grade"), "| required", card.get("requiredApprovers"),
              "| candidate", card.get("candidateId"), "| diff", bool(card.get("diff")))
        user = DRIVER if card["approver"] == "DRIVER" else MANAGER
        out = await decide(cmd, card, user)
    end = [e for e in events if e["taskId"] == cmd["taskId"]][-1]
    print("  end:", out["status"], "|", (end.get("answer") or f"{end.get('code')}: {end.get('error')}")[:200])

    # rag 결과 모양 (Harness sources가 읽는 모양인지)
    rag_tool = next(t for t in tools if t.name == "rag_search_docs")
    result = await rag_tool.ainvoke({"type": "tool_call", "name": rag_tool.name, "args": {"query": "불꽃축제 대여 중지"}, "id": "x"})
    data = json.loads(result.content if isinstance(result.content, str) else result.content[0]["text"])
    print("\nrag_search_docs keys:", sorted(data) if isinstance(data, dict) else type(data).__name__,
          "| first chunk keys:", sorted(data["chunks"][0]) if isinstance(data, dict) and data.get("chunks") else None)
    from harness.contract import sources
    from langchain_core.messages import ToolMessage
    print("harness sources from it:", sources([ToolMessage(content=json.dumps(data, ensure_ascii=False), name="rag_search_docs",
                                                           tool_call_id="x")], config.observability.tool_labels))
    print("\nschema check:", all(e.get("taskId") for e in events), len(events), "events")


asyncio.run(main(*sys.argv[1:]))
