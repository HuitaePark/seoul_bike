"""03a through the real backend with the agent on the real business-mcp/rag-mcp (role 3 branch, fake /internal).

    python e2e_real_mcp.py <seed-password> <scratch e2e dir> <approve_url>

A: approve without the /internal approval record → change tool 403 → task FAILED with code (never "executed").
B: record the approval in business-mcp's fake /internal first (what backend /internal will do, M3) → DONE.
"""
import sys
import time

import httpx

sys.path.insert(0, sys.argv[2])
from e2e_03a import sse  # noqa: E402

SHIFT = "gwangjin-seongdong:2026-10-21:AM"
approve_url = sys.argv[3]
c = httpx.Client(base_url="http://localhost:8080", timeout=30)
c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"loginId": "manager", "password": sys.argv[1]}).json()["accessToken"]

for case, record in (("A no /internal record", False), ("B with /internal record", True)):
    t0 = time.time()
    tid = c.post("/api/ai/tasks", json={"type": "PLAN_DRAFT", "shiftId": SHIFT}).json()["taskId"]
    ev, fr = sse(c, tid, timeout=120)
    card = ev[-1].get("approval")
    print(f"\n{case}: {[f['event'] for f in fr]}", "|", card and card["summary"], "| routes", card and len(card.get("routes") or []))
    if not card:
        print("  end:", ev[-1])
        continue
    if record:
        httpx.post(approve_url, json={"approvalId": card["approvalId"], "role": "MANAGER", "taskId": tid}).raise_for_status()
    r = c.post(f"/api/ai/tasks/{tid}/approval", json={"approvalId": card["approvalId"], "decision": "APPROVE"})
    ev2, fr2 = sse(c, tid, after=fr[-1]["id"], until=("DONE", "FAILED"), timeout=120)
    end = ev2[-1]
    tool_end = [e for e in ev2 if e["type"] == "TOOL_END"]
    print(f"  approve {r.status_code} → {[f['event'] for f in fr2]} in {time.time() - t0:.1f}s | tool ok:",
          [e["tool"].get("ok") for e in tool_end])
    print("  end:", end["type"], end.get("code"), "|", (end.get("answer") or end.get("error") or "")[:170].replace("\n", " "))
    task = c.get(f"/api/ai/tasks/{tid}").json()
    print("  task:", task["status"], "| error:", (task.get("error") or "")[:80])
