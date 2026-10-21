# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 근무조 상태 요약(brief)을 코드로 만든다. 숫자는 상태·보고의 값을 그대로 옮긴다.

"""상태 요약 (docs/AGENT-GRAPH.md 2.3). 답변 근거(sources)는 Harness가 만든다 (harness.contract.sources)."""
from __future__ import annotations

ISSUE_TEXT = {"BLOCKED": "진입 불가", "CONSTRUCTION": "공사", "INVENTORY_MISMATCH": "재고 불일치", "OTHER": "기타"}


def render_report(report: dict) -> str:
    """현장 보고 한 줄. 기사 앱이 보낸 값만 쓴다."""
    parts = [report.get("truckId") or "트럭 미상", report.get("stationId") or "대여소 미상"]
    if report.get("actualBikes") is not None:
        parts.append(f"실제 재고 {report['actualBikes']}대")
    if report.get("issue"):
        parts.append(ISSUE_TEXT.get(report["issue"], report["issue"]))
    return " ".join(parts)


def make_brief(state: dict) -> str:
    """Harness `Task.brief`. 모델이 근무조 전체 상황을 알도록 시스템 프롬프트 끝에 붙는다 (HARNESS 1.4)."""
    lines = [f"근무조: {state.get('shift_id', '')}"]
    plan = state.get("plan") or {}
    line = f"활성 계획: {plan.get('active') or '없음'}"
    if plan.get("proposed"):
        line += f", 승인 대기 계획: {plan['proposed']}"
    lines.append(line)
    pending = [a["card"] for a in (state.get("approvals") or {}).values() if a["status"] == "PENDING"]
    if pending:
        lines.append("승인 대기: " + "; ".join(f"{c['summary']} ({c['approver']})" for c in pending))
    reports = (state.get("reports") or [])[-5:]
    if reports:
        lines.append("최근 현장 보고: " + "; ".join(render_report(r) for r in reports))
    return "\n".join(lines)
