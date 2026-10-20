# Author: 정유은
# Date: 20OCT2026
# Purpose: rag-mcp 도구(search_docs)와 Resource(doc://{doc_id}, doc://index). 문서를 읽기만 한다.

"""rag-mcp: search_docs 도구와 doc:// Resource (docs/MCP.md 4절). 읽기만 한다."""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ResourceError
from pydantic import Field

from .store import DocStore

Json = dict[str, Any]

INSTRUCTIONS = """\
따릉이 재배치 근거 문서 검색. 답변에 근거를 쓸 때는 chunk의 citation과 section을 함께 밝힌다.
basis가 primary면 팀 저장소(PRD 등) 내용, supplementary면 저장소 밖 자료(논문, 서울시 보도자료)다.
두 등급이 다른 값을 주면 primary를 따른다. data_period가 오래된 수치는 참고 기준으로만 인용한다.
status에 추정치·가상 값 같은 한계가 적혀 있으면 답변에도 함께 밝힌다.
원문 전체는 Resource doc://{doc_id}, 문서 목록은 doc://index에서 읽는다.
"""

DocType = Literal["ops_standard", "seoul_notice", "reverse_analysis", "handover", "reference_paper", "general"]
READ = {"readOnlyHint": True, "idempotentHint": True, "openWorldHint": False}


def build_server(store: DocStore, *, max_chars: int = 4000) -> FastMCP:
    mcp = FastMCP("rag-mcp", instructions=INSTRUCTIONS, version="0.1.0", mask_error_details=True)

    @mcp.tool(annotations=READ, tags={"read"})
    def search_docs(
        query: Annotated[str, Field(min_length=1, max_length=500, description="자연어 질문")],
        top_k: Annotated[int, Field(ge=1, le=10)] = 5,
        doc_types: Annotated[
            list[DocType] | None,
            Field(description="ops_standard(운영 기준), seoul_notice(서울시 공지), reverse_analysis(역산 분석), "
                              "handover(인수인계 양식), reference_paper(외부 논문), general(종류 미지정) 중 골라 검색. 비우면 전체"),
        ] = None,
    ) -> Json:
        """근거 문서에서 질문과 관련된 chunk를 찾는다.

        반환: chunks[] (doc_id, doc_type, title, section, text, citation, basis(primary·supplementary), status(문서 상태·한계), data_period,
        version, score(0~1, 가장 관련 높은 chunk = 1), uri). 원문은 uri의 doc_id로 Resource doc://{doc_id}를 읽는다.
        """
        chunks = store.search(query, top_k, list(doc_types) if doc_types else None)
        result: Json = {**store.info(), "chunks": chunks, "truncated": False}
        if not chunks:
            hint = "doc_types를 빼고 " if doc_types else "다른 표현으로 "
            result["message"] = f"관련 문서를 찾지 못했습니다. {hint}다시 검색해 보세요."
            return result
        while len(json.dumps(result, ensure_ascii=False)) > max_chars and len(result["chunks"]) > 1:
            result["chunks"].pop()
            result["truncated"] = True
        return result

    @mcp.resource("doc://index", mime_type="application/json", description="근거 문서 목록")
    def doc_index() -> str:
        return json.dumps(store.list_docs(), ensure_ascii=False)

    @mcp.resource("doc://{doc_id}", mime_type="text/markdown", description="근거 문서 원문 (front matter 포함)")
    def doc_source(doc_id: str) -> str:
        raw = store.get_doc_raw(doc_id.split("#", 1)[0])
        if raw is None:
            raise ResourceError(f"'{doc_id}' 문서가 없습니다. 문서 목록은 doc://index에서 확인하세요.")
        return raw

    return mcp
