# Author: 정유은
# Date: 21OCT2026
# Purpose: 실제 MCP 서버(business-mcp·rag-mcp)의 결과 모양을 Harness가 읽는지 확인한다 (ISSUES ISS-23).

import json
import unittest

from langchain_core.messages import ToolMessage

from harness.contract import sources

RAG_RESULT = {  # rag-mcp search_docs 결과 (docs/MCP.md 4.1)
    "as_of": "2026-10-20T12:00:00+09:00",
    "source": "pgvector:rag_chunk (cosine + bm25)",
    "embedding": {"model": "intfloat/multilingual-e5-large", "dim": 1024},
    "chunks": [
        {"doc_id": "notice-seoul-fireworks-2025", "title": "서울시 공지", "section": "2. 중단 내용",
         "uri": "doc://notice-seoul-fireworks-2025#2", "text": "9월 26일 19시 ~ 9월 28일 09시", "basis": "supplementary",
         "citation": "서울시 보도자료", "score": 1.0},
        {"doc_id": "notice-seoul-fireworks-2025", "title": "서울시 공지", "section": "4. 운영 기준에 따른 처리",
         "uri": "doc://notice-seoul-fireworks-2025#4", "text": "배치 대상에서 뺀다", "basis": "primary",
         "citation": "PRD 6.4", "score": 0.9},
    ],
    "truncated": False,
}


def tool(name: str, content: object) -> ToolMessage:
    return ToolMessage(content=json.dumps(content, ensure_ascii=False), name=name, tool_call_id=name)


class SourcesShapeTest(unittest.TestCase):
    def test_rag_mcp_chunks_become_doc_sources_per_section(self):
        found = sources([tool("rag_search_docs", RAG_RESULT)], {})
        self.assertEqual([s["uri"] for s in found], ["doc://notice-seoul-fireworks-2025#2", "doc://notice-seoul-fireworks-2025#4"])
        self.assertEqual(found[0]["title"], "서울시 공지 · 2. 중단 내용")
        self.assertTrue(all(s["kind"] == "DOC" and s["excerpt"] for s in found))

    def test_list_shape_still_works(self):
        found = sources([tool("rag_search_docs", [{"doc_id": "doc://a", "source": "A", "text": "본문"}])], {})
        self.assertEqual(found, [{"kind": "DOC", "title": "A", "uri": "doc://a", "excerpt": "본문"}])

    def test_tool_results_are_tool_sources(self):
        found = sources([tool("business_get_current_plan", {"planId": "P-1"})], {"business_get_current_plan": "계획 조회 중"})
        self.assertEqual(found, [{"kind": "TOOL", "title": "계획 조회", "uri": None, "excerpt": None}])


if __name__ == "__main__":
    unittest.main()
