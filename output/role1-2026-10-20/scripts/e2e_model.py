import sys, time
sys.path.insert(0, sys.argv[2])
from e2e_03a import sse, SHIFT
import httpx
c = httpx.Client(base_url="http://localhost:8080", timeout=30)
r = c.post("/api/auth/login", json={"loginId": "manager", "password": sys.argv[1]}); r.raise_for_status()
c.headers["Authorization"] = "Bearer " + r.json()["accessToken"]
for kind, text in [("QUESTION", "지금 가장 위험한 대여소 두 곳과 이유는?"), ("PLAN_DRAFT", None)]:
    t0 = time.time()
    body = {"type": kind, "shiftId": SHIFT, **({"text": text} if text else {})}
    tid = c.post("/api/ai/tasks", json=body).json()["taskId"]
    ev, fr = sse(c, tid, timeout=180)
    print(f"{kind}: {time.time()-t0:.1f}s", [f["event"] for f in fr if f["event"] != "TOKEN"], "tokens:", sum(f["event"] == "TOKEN" for f in fr))
    last = ev[-1]
    if last["type"] == "APPROVAL_REQUIRED":
        card = last["approval"]
        print("  card:", card["kind"], card["approver"], card["summary"])
        c.post(f"/api/ai/tasks/{tid}/approval", json={"approvalId": card["approvalId"], "decision": "APPROVE"}).raise_for_status()
        ev2, fr2 = sse(c, tid, after=fr[-1]["id"], until=("DONE", "FAILED"), timeout=180)
        last = ev2[-1]
        print(f"  after approve: {time.time()-t0:.1f}s", [f["event"] for f in fr2 if f["event"] != "TOKEN"])
    print("  end:", last["type"], (last.get("answer") or last.get("error") or "")[:300].replace("\n", " "))
    print("  sources:", [s["title"] for s in last.get("sources") or []])
