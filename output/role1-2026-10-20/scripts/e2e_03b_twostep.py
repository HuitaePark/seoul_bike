"""03b two-step approval through the real backend, with the FIELD_REPORT command shaped as the backend should send it
after G13 (reportId + report + driver truck). Steps that touch Kafka/compose are done by the caller (see phase arg).

phase "create": login, make a TR-1 driver, create a FIELD_REPORT task (backend sends a command without reportId),
                print JSON {taskId, traceId, driverLogin, driverPw, driverId}
phase "command": print the fixed TaskCommand JSON for that task (stdin: the create output)
phase "approve": walk the cards: wrong person must get 403, right person 202, until DONE (stdin: the create output)
"""
import json
import secrets
import sys
import uuid
from datetime import UTC, datetime

import httpx

sys.path.insert(0, sys.argv[3])
from e2e_03a import SHIFT, sse  # noqa: E402

BASE = "http://localhost:8080"


def client(login, password):
    c = httpx.Client(base_url=BASE, timeout=30)
    r = c.post("/api/auth/login", json={"loginId": login, "password": password})
    r.raise_for_status()
    body = r.json()
    c.headers["Authorization"] = "Bearer " + body["accessToken"]
    return c, body["user"]


phase, seed = sys.argv[1], sys.argv[2]
manager, _ = client("manager", seed)

if phase == "create":
    login, pw = "e2e-tr1-" + secrets.token_hex(2), "e2e-" + secrets.token_hex(6)
    r = manager.post("/api/admin/users", json={"loginId": login, "password": pw, "name": "기사 TR1", "role": "DRIVER",
                                                "truckId": "TR-1"})
    r.raise_for_status()
    _, user = client(login, pw)
    r = manager.post("/api/ai/tasks", json={"type": "FIELD_REPORT", "shiftId": SHIFT})
    r.raise_for_status()
    task = manager.get(f"/api/ai/tasks/{r.json()['taskId']}").json()
    print(json.dumps({"taskId": task["taskId"], "traceId": task["traceId"], "driverLogin": login, "driverPw": pw,
                      "driverId": user["id"]}))
elif phase == "command":
    info = json.loads(sys.stdin.read())
    print(json.dumps({
        "eventId": str(uuid.uuid4()), "schemaVersion": 1, "occurredAt": datetime.now(UTC).isoformat(),
        "shiftId": SHIFT, "traceId": info["traceId"], "taskId": info["taskId"], "threadId": f"{SHIFT}:{info['taskId']}",
        "type": "FIELD_REPORT", "reportId": "R-E2E-1",
        "report": {"stationId": "ST-2005", "truckId": "TR-1", "actualBikes": 2, "loaded": 0, "unloaded": 0,
                   "issue": "BLOCKED", "memo": ""},
        "requestedBy": {"userId": info["driverId"], "role": "DRIVER", "truckId": "TR-1"},
    }, ensure_ascii=False))
else:
    info = json.loads(sys.stdin.read())
    driver, _ = client(info["driverLogin"], info["driverPw"])
    who = {"MANAGER": (manager, driver, "manager"), "DRIVER": (driver, manager, "driver TR-1")}
    tid, after, cards = info["taskId"], 0, []
    for _ in range(4):
        ev, fr = sse(manager, tid, after=after, timeout=120)
        after = fr[-1]["id"]
        print([(f["id"], f["event"]) for f in fr])
        last = ev[-1]
        if last["type"] != "APPROVAL_REQUIRED":
            print("end:", last["type"], (last.get("answer") or last.get("error") or "").replace("\n", " | ")[:200])
            break
        card = last["approval"]
        cards.append(card)
        print(f"  card {card['kind']} approver={card['approver']} required={card.get('requiredApprovers')} "
              f"candidate={card.get('candidateId')} truck={card.get('truckId')}\n   {card['summary']}")
        right, wrong, name = who[card["approver"]]
        r = wrong.post(f"/api/ai/tasks/{tid}/approval", json={"approvalId": card["approvalId"], "decision": "APPROVE"})
        print("  wrong person:", r.status_code, r.json().get("code"))
        r = right.post(f"/api/ai/tasks/{tid}/approval", json={"approvalId": card["approvalId"], "decision": "APPROVE"})
        print(f"  {name} approves:", r.status_code, r.text[:120] if r.status_code != 202 else "")
    task = manager.get(f"/api/ai/tasks/{tid}").json()
    print("task:", task["status"], "| cards:", [c["approver"] for c in cards])
