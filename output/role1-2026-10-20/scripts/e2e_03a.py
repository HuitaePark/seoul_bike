"""03a end-to-end through the real backend: login → PLAN_DRAFT → SSE → exclude → new card → approve → DONE.

Usage: python e2e_03a.py <seed-password>   (backend on :8080, agent consuming ai.tasks)
"""
import json
import sys
import time

import httpx

BASE = "http://localhost:8080"
SHIFT = "yeongdeungpo:2026-10-24:PM"  # evaluation/fake_mcp.py SHIFT_ID


def sse(client, task_id, after=0, until=("APPROVAL_REQUIRED", "DONE", "FAILED"), timeout=60):
    """Read the task stream from Last-Event-ID `after` until one of `until` arrives. Returns (events, frames)."""
    headers = {"Accept": "text/event-stream"}
    if after:
        headers["Last-Event-ID"] = str(after)
    events, frames = [], []
    deadline = time.time() + timeout
    with client.stream("GET", f"/api/ai/tasks/{task_id}/stream", headers=headers, timeout=timeout) as r:
        r.raise_for_status()
        block = {}
        for line in r.iter_lines():
            if time.time() > deadline:
                raise TimeoutError("SSE timeout")
            if line.startswith("id:"):
                block["id"] = int(line[3:].strip())
            elif line.startswith("event:"):
                block["event"] = line[6:].strip()
            elif line.startswith("data:"):
                block["data"] = json.loads(line[5:].strip())
            elif line == "" and "data" in block:
                frames.append(block)
                events.append(block["data"])
                if block["data"]["type"] in until:
                    return events, frames
                block = {}
    return events, frames


def main(password):
    c = httpx.Client(base_url=BASE, timeout=30)
    tok = c.post("/api/auth/login", json={"loginId": "manager", "password": password})
    tok.raise_for_status()
    c.headers["Authorization"] = f"Bearer {tok.json()['accessToken']}"
    print("login:", tok.json()["user"]["role"])

    r = c.post("/api/ai/tasks", json={"type": "PLAN_DRAFT", "shiftId": SHIFT})
    print("create:", r.status_code, r.json())
    assert r.status_code == 202
    task_id = r.json()["taskId"]

    events, frames = sse(c, task_id)
    print("stream 1:", [(f["id"], f["event"]) for f in frames])
    assert [f["id"] for f in frames] == list(range(1, len(frames) + 1)), "seq from backend must be 1..n"
    card = events[-1]["approval"]
    print("card 1:", card["kind"], card["approver"], card["summary"], "| routes:", len(card.get("routes") or []))
    assert events[-1]["type"] == "APPROVAL_REQUIRED" and card["kind"] == "SUBMIT_PLAN"
    task = c.get(f"/api/ai/tasks/{task_id}").json()
    print("task:", task["status"], (task.get("pendingApproval") or {}).get("approvalId") == card["approvalId"])
    assert task["status"] == "WAITING_APPROVAL"

    # UR-04: exclude one station → recompute → new card on the same task
    stop = card["routes"][0]["stops"][0]["stationId"] if card.get("routes") else "ST-2007"
    r = c.post(f"/api/ai/tasks/{task_id}/approval",
               json={"approvalId": card["approvalId"], "decision": "REJECT", "excludeStationIds": [stop],
                     "comment": "이 대여소는 오늘 공사 중"})
    print("exclude:", r.status_code, stop)
    assert r.status_code == 202, r.text
    events2, frames2 = sse(c, task_id, after=frames[-1]["id"])
    print("stream 2:", [(f["id"], f["event"]) for f in frames2])
    card2 = events2[-1]["approval"]
    print("card 2:", card2["summary"], card2["reasons"])
    assert events2[-1]["type"] == "APPROVAL_REQUIRED" and card2["approvalId"] != card["approvalId"]
    assert any(stop in x for x in card2["reasons"])

    # stale decision on the first card → 409
    r = c.post(f"/api/ai/tasks/{task_id}/approval", json={"approvalId": card["approvalId"], "decision": "APPROVE"})
    print("late decision:", r.status_code, r.json().get("code") if r.headers.get("content-type", "").startswith("application/problem") else r.text[:80])
    assert r.status_code == 409

    r = c.post(f"/api/ai/tasks/{task_id}/approval", json={"approvalId": card2["approvalId"], "decision": "APPROVE"})
    print("approve:", r.status_code)
    assert r.status_code == 202
    events3, frames3 = sse(c, task_id, after=frames2[-1]["id"], until=("DONE", "FAILED"))
    print("stream 3:", [(f["id"], f["event"]) for f in frames3])
    print("answer:", events3[-1].get("answer") or events3[-1].get("error"))
    assert events3[-1]["type"] == "DONE"
    task = c.get(f"/api/ai/tasks/{task_id}").json()
    print("task:", task["status"], "sources:", [s["title"] for s in task.get("sources") or []])
    assert task["status"] == "DONE"

    # replay: reconnect without Last-Event-ID gets the whole history with ids 1..n
    allev, allfr = sse(c, task_id, until=("DONE", "FAILED"))
    ids = [f["id"] for f in allfr]
    print("replay ids:", ids)
    assert ids == list(range(1, len(ids) + 1))
    print("E2E 03a OK")


if __name__ == "__main__":
    main(sys.argv[1])
