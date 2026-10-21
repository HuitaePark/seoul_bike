# Author: Jung Minkyu
# Date: 20OCT2026
# Purpose: 승인 카드 둘레에서 그래프가 하는 일: RESUME 정리(의견 마스킹·감싸기, 제외 ID 검사), 계획 카드·근거 보강, 작업 사이 대체.

"""승인 (docs/AGENT-GRAPH.md 3.2, 3.4).

카드를 만들고 승인 순서(`requiredApprovers`)를 따르는 일은 Harness가 한다 (`harness.contract`, HARNESS 3.6).
그래프는 그 앞뒤만 맡는다.
- RESUME 정리: 승인자 의견은 사람이 쓴 글이라 기사 이름·연락처를 가리고 `<user_input>`으로 감싼다.
  Harness `decision_from_resume`은 의견을 그대로 모델에게 넘긴다. 거점 제외는 계획 카드에서만, ID 모양만 받는다.
- 계획 카드 보강: 트럭별 경로(`routes`, 확장 G9)와 포기·제외 사유를 도구 결과에서 옮겨 화면이 '일부 제외'를 고를 수 있게 한다.
  요약에는 트럭·대여소 수를 붙인다. 정류장 `eta`는 계획 도구 결과에 있으면 그대로 옮기고, 없으면 만들지 않는다
  (business-mcp가 응답 길이를 맞추려고 먼저 빼는 필드다).
- 작업 사이 대체: 같은 대상의 새 카드가 나오면 다른 작업의 대기 카드를 닫는다 (G10). backend는 같은 작업 안에서만 대체한다.
- 근거 보강: rag-mcp `search_docs`는 `{chunks: [...]}`를 준다(MCP 명세 4.1). Harness `sources`는 목록 모양만 읽어 문서 근거가
  빠지므로, 그래프가 DONE의 `sources`에 DOC를 채운다 (ISS-23). Harness가 고치면 중복만 걸러지고 바뀌는 것이 없다.
"""
from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterable
from typing import Any

from langchain_core.messages import BaseMessage, ToolMessage

from harness.prompts import wrap_user_input

log = logging.getLogger(__name__)

# 거점 제외 ID는 모델에게 가는 문장에 그대로 들어간다. ID 모양이 아닌 값(문장 끼워 넣기)은 버린다.
STATION_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def exclusions(card: dict, ids: Iterable[str]) -> list[str]:
    """쓸 수 있는 거점 제외 ID. 계획 승인 카드에만 쓰고(openapi `ApprovalDecision.excludeStationIds`), ID 모양만 받는다."""
    ids = list(ids)
    if not ids:
        return []
    if card["kind"] != "SUBMIT_PLAN":
        log.warning("계획 승인이 아닌 카드(%s)의 거점 제외는 무시한다: %s", card["kind"], ids)
        return []
    bad = [i for i in ids if not STATION_ID.fullmatch(i)]
    if bad:
        log.warning("ID 모양이 아닌 거점 제외는 버린다: %s", bad)
    return [i for i in ids if STATION_ID.fullmatch(i)]


def clean_resume(resume: dict, card: dict, mask: Callable[[str], str]) -> dict:
    """계약 RESUME → Harness `resume_command`에 넘길 값. 의견은 가리고 감싸고, 제외 ID는 거른다."""
    out = {k: v for k, v in resume.items() if k not in {"comment", "excludeStationIds"}}
    exclude = exclusions(card, resume.get("excludeStationIds") or [])
    if exclude:
        out["excludeStationIds"] = exclude
    comment = (resume.get("comment") or "").strip()
    if comment:
        out["comment"] = "승인자 의견:\n" + wrap_user_input(mask(comment))
    return out


def tool_results(history: Iterable[BaseMessage]) -> list[Any]:
    """성공한 도구 결과(JSON)."""
    out = []
    for m in history:
        if isinstance(m, ToolMessage) and m.status != "error":
            try:
                out.append(json.loads(m.content))
            except (json.JSONDecodeError, TypeError):
                continue
    return out


def enrich_card(card: dict, results: Iterable[Any], excluded: Iterable[str] = ()) -> dict:
    """계획 카드에 트럭별 경로(openapi `Route`, 확장 G9)와 포기·제외 사유를 붙인다. 숫자·ID는 도구 결과를 옮긴 것이다."""
    if card.get("kind") != "SUBMIT_PLAN":
        return card
    plan = next((r["plan"] for r in reversed(list(results))
                 if isinstance(r, dict) and isinstance(r.get("plan"), dict) and r["plan"].get("planId") == card.get("planId")),
                None)
    if not plan:
        return card
    reasons = list(card.get("reasons") or [])
    reasons += [f"포기 {u['stationId']}: {u['reason']}" if u.get("reason") else f"포기 {u['stationId']}"
                for u in plan.get("unserved", [])]
    excluded = list(dict.fromkeys([*(plan.get("excluded") or []), *excluded]))
    if excluded:
        reasons.append(f"제외한 대여소: {', '.join(excluded)}")
    routes = [{"truckId": r["truckId"], "stops": r.get("stops", [])} for r in plan.get("routes", []) if r.get("stops")]
    if plan.get("truncated"):
        shown = sum(len(r["stops"]) for r in routes)
        reasons.append(f"카드 경로는 정류장 {shown}/{plan.get('totalStops', shown)}곳만 실렸다 (MCP 응답 길이 제한)")
    return {**card, "summary": plan_summary(card, plan, routes), "reasons": reasons, "routes": routes}


def plan_summary(card: dict, plan: dict, routes: list[dict]) -> str:
    """계획 카드 요약에 움직이는 트럭 수·들르는 대여소 수를 붙인다 (#91). Harness 요약은 계획 ID뿐이다.
    business-mcp는 응답이 길면 정류장의 eta·이름을 빼고 뒤쪽 정류장을 자른다. 그때도 대여소 수는 `totalStops`(자르기 전)로 센다.
    빈 트럭은 따로 센다. 회수할 자전거보다 배치 수요가 많으면 트럭 대부분이 놀 수 있어서 관리자가 바로 보게 한다."""
    stops = plan.get("totalStops") or sum(len(r["stops"]) for r in routes)
    idle = len(plan.get("routes", [])) - len(routes)
    summary = f"계획 {card.get('planId')} 활성화: 트럭 {len(routes)}대, 대여소 {stops}곳"
    return summary + (f", 빈 트럭 {idle}대" if idle else "")


def doc_sources(history: Iterable[BaseMessage]) -> list[dict]:
    """rag 도구 결과의 chunk → 계약 `Source`(kind DOC). uri는 `doc://{docId}#{절}`(rag-mcp Resource), 제목에 절 이름을 붙인다."""
    found: list[dict] = []
    seen: set[str] = set()
    for m in history:
        if not isinstance(m, ToolMessage) or m.status == "error" or not (m.name or "").startswith("rag_"):
            continue
        try:
            data = json.loads(m.content)
        except (json.JSONDecodeError, TypeError):
            continue
        for chunk in data.get("chunks") or [] if isinstance(data, dict) else []:
            uri = chunk.get("uri") or (f"doc://{chunk['doc_id']}" if chunk.get("doc_id") else None)
            if not uri or uri in seen:
                continue
            seen.add(uri)
            title = chunk.get("title") or uri
            if chunk.get("section"):  # 같은 문서의 다른 절이 근거 패널에서 구별되게
                title = f"{title} · {chunk['section']}"
            found.append({"kind": "DOC", "title": title, "uri": uri, "excerpt": (chunk.get("text") or "")[:120] or None})
    return found


def merge_sources(sources: list[dict], extra: Iterable[dict]) -> list[dict]:
    """빠진 근거만 더한다. 같은 종류·uri(없으면 제목)면 같은 근거로 본다. 문서 근거를 앞에 둔다."""
    merged: list[dict] = []
    have: set[tuple] = set()
    for source in [*sources, *extra]:
        key = (source["kind"], source.get("uri") or source["title"])
        if key not in have:
            have.add(key)
            merged.append(source)
    return [s for s in merged if s["kind"] == "DOC"] + [s for s in merged if s["kind"] != "DOC"]


def same_target(old: dict, new: dict) -> bool:
    """새 승인 카드가 다른 작업의 대기 카드를 대체하는가. 계획 활성화끼리, 같은 트럭의 경로 변경끼리 대체한다."""
    if old["kind"] != new["kind"]:
        return False
    if new["kind"] == "SUBMIT_PLAN":
        return True
    if new["kind"] == "ROUTE_CHANGE":
        return old.get("truckId") == new.get("truckId")
    return False


def card_step(card: dict) -> int:
    """경로 변경 카드가 승인 순서의 몇 번째인가 (0부터). 체크포인트에 남겨 재시작 뒤에도 같은 카드로 재개한다 (ISS-19)."""
    order = card.get("requiredApprovers") or []
    return order.index(card["approver"]) if card.get("kind") == "ROUTE_CHANGE" and card["approver"] in order else 0
