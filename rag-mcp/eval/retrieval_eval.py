# Author: 정유은
# Date: 20OCT2026
# Purpose: 근거 문서 검색 품질 점검. 질문마다 정답 문서·절을 정해 두고, 키워드 검색(메모리)과 pgvector 혼합 검색의 적중률을 비교한다.

"""검색 품질 점검 (rag-mcp/eval/README.md).

    python eval/retrieval_eval.py                 # 메모리 BM25만
    VECTOR_DSN=... python eval/retrieval_eval.py  # pgvector 색인(rag-indexer로 미리 만든 것)도 함께

정답은 "질문에 답하는 근거가 있는 문서와 절"이다. 표현을 문서와 일부러 다르게 써서 낱말이 겹치지 않을 때도 찾는지 본다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from rag_mcp.docs import load_docs
from rag_mcp.store import InMemoryKeywordStore

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"

# (질문, 정답 목록). 정답은 (doc_id, 절 이름에 들어 있는 말). 답이 되는 근거가 여러 절에 있으면 모두 정답으로 둔다.
CASES = [
    ("트럭 한 대에 자전거를 최대 몇 대 실을 수 있어?", [("ops-rebalancing-standard", "트럭과 경로")]),
    ("여의도 축제 날 공공자전거를 언제부터 언제까지 못 빌렸어?",
     [("notice-seoul-fireworks-2025", "중단 내용"), ("ops-rebalancing-standard", "제약 조건")]),
    ("대여 중지된 대여소에 자전거를 가져다 놔야 해?",
     [("ops-rebalancing-standard", "제약 조건"), ("notice-seoul-fireworks-2025", "운영 기준에 따른 처리")]),
    ("기사가 운전하는 동안 변경안 알림을 띄워도 돼?", [("ops-rebalancing-standard", "현장 보고")]),
    ("관리자 승인이 필요한 큰 변경은 어떤 경우야?", [("ops-rebalancing-standard", "변경 등급")]),
    ("수요 예측이 실제랑 보통 몇 대 정도 틀려?",
     [("ref-forecast-kim-2023", "예측 정확도"), ("ops-rebalancing-standard", "목표 재고")]),
    ("오전 근무조 계획 초안은 몇 시에 만들어?", [("ops-rebalancing-standard", "근무조")]),
    ("다음 근무조에 넘길 때 처리 못 한 대여소는 어떻게 적어?", [("handover-shift-summary-template", "")]),
    ("논문 데이터가 몇 년도 거라 인용할 때 조심해야 해?", [("ref-forecast-kim-2023", "인용 시 주의")]),
    ("재계산은 현장 재고가 몇 대 차이 날 때 해?", [("ops-rebalancing-standard", "재계산 기준")]),
]


# 확인용 질문. 위 CASES로 문서 표현을 고친 뒤 새로 쓰고 한 번만 돌렸다 (조정에 쓰지 않은 질문).
HOLDOUT = [
    ("밤 10시 이후에도 재배치 트럭이 다녀?", [("ops-rebalancing-standard", "근무조")]),
    ("거치대가 다 찼으면 반납을 못 하는 거야?", [("ops-rebalancing-standard", "목표 재고")]),
    ("불꽃축제 날 한강대교 버스는 어떻게 다녔어?", [("notice-seoul-fireworks-2025", "교통 통제")]),
    ("현장 재고가 앱이랑 다르면 어떤 제약으로 바꿔?", [("ops-rebalancing-standard", "제약 조건")]),
    ("트럭이 부족해서 못 간 대여소는 관리자에게 어떻게 보여줘?", [("ops-rebalancing-standard", "트럭과 경로")]),
    ("그 논문은 경로 최적화도 다뤘어?", [("ref-forecast-kim-2023", "연구 개요"), ("ref-forecast-kim-2023", "우리 설계")]),
    ("인수인계 메모에서 숫자는 누가 채워?", [("handover-shift-summary-template", "작성 규칙")]),
    ("재경로는 몇 초 안에 나와야 해?", [("ops-rebalancing-standard", "성능 기준")]),
]


def evaluate(name: str, store, cases=None) -> tuple[int, int]:
    cases = cases or CASES
    hit1 = hit3 = 0
    print(f"\n## {name}")
    for q, gold in cases:
        res = store.search(q, 3, None)
        ok = [any(r["doc_id"] == d and s in r["section"] for d, s in gold) for r in res]
        hit1 += bool(ok[:1] and ok[0])
        hit3 += any(ok)
        top = res[0] if res else {"doc_id": "-", "section": "-"}
        mark = "1위" if ok[:1] and ok[0] else ("3위 안" if any(ok) else "못 찾음")
        print(f"- [{mark}] {q} -> {top['doc_id']} / {top['section']}")
    print(f"적중: 1위 {hit1}/{len(cases)}, 3위 안 {hit3}/{len(cases)}")
    return hit1, hit3


def main() -> None:
    docs = load_docs(Path(os.environ.get("RAG_DOCS_DIR", DOCS_DIR)))
    stores = {"메모리 BM25 (키워드)": InMemoryKeywordStore(docs)}
    if os.environ.get("VECTOR_DSN") or os.environ.get("PGHOST"):
        from rag_mcp.embedding import make_embedder
        from rag_mcp.indexer import connect
        from rag_mcp.pgvector_store import PgVectorStore

        emb = make_embedder()
        stores[f"pgvector 혼합 ({emb.model}, {emb.dim}차원)"] = PgVectorStore(connect, emb)
    for label, cases in (("점검 질문", CASES), ("확인용 질문 (조정에 안 씀)", HOLDOUT)):
        for name, store in stores.items():
            evaluate(f"{label} / {name}", store, cases)


if __name__ == "__main__":
    sys.exit(main())
