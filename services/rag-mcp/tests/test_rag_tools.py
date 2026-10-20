# Author: 정유은
# Date: 20OCT2026
# Purpose: rag-mcp 도구·Resource 단위 테스트. 저장소의 docs/rag 문서를 인메모리 Client로 검색한다.

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastmcp import Client

from rag_mcp.docs import DOC_TYPES, chunk_doc, load_docs, parse_doc
from rag_mcp.server import build_server
from rag_mcp.store import InMemoryKeywordStore

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "rag"


@pytest.fixture(scope="module")
def docs():
    return load_docs(DOCS_DIR)


@pytest.fixture
def client(docs):
    return Client(build_server(InMemoryKeywordStore(docs)))


async def search(client: Client, **args) -> dict:
    res = await client.call_tool("search_docs", args)
    return res.structured_content


# ── 문서와 Chunking ──────────────────────────────────────

def test_all_docs_have_valid_front_matter(docs):
    assert {d.doc_type for d in docs} == set(DOC_TYPES)
    for d in docs:
        assert any(s["role"] == "primary" for s in d.meta["sources"])


def test_supplementary_paragraphs_are_split_out(docs):
    ops = next(d for d in docs if d.doc_id == "ops-rebalancing-standard")
    chunks = chunk_doc(ops)
    supp = [c for c in chunks if c.basis == "supplementary"]
    assert supp and all("[보조]" in c.text for c in supp)
    assert all("김유신" in c.citation or "서울시" in c.citation for c in supp)
    assert all("[보조]" not in line.lstrip("- ")[:6]
               for c in chunks if c.basis == "primary" for line in c.text.splitlines())


def test_reference_paper_is_supplementary_with_paper_citation(docs):
    paper = next(d for d in docs if d.doc_type == "reference_paper")
    chunks = chunk_doc(paper)
    assert {c.basis for c in chunks} == {"supplementary"}
    assert all("김유신" in c.citation for c in chunks)


def test_chunks_keep_source_line(docs):
    ops = next(d for d in docs if d.doc_id == "ops-rebalancing-standard")
    for c in chunk_doc(ops):
        if c.section != "개요":
            assert "출처:" in c.text, c.chunk_id


def test_chunks_stay_short(docs):
    for d in docs:
        for c in chunk_doc(d):
            assert len(c.text) <= 1600, c.chunk_id


def test_parse_rejects_missing_front_matter(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("# 제목만 있음\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_doc(p)


# ── search_docs ──────────────────────────────────────────

async def test_search_returns_chunks_with_citation(client):
    async with client:
        res = await search(client, query="불꽃축제 대여 반납 중지")
    top = res["chunks"][0]
    assert top["doc_id"] == "notice-seoul-fireworks-2025"
    assert {"citation", "basis", "section", "uri", "score", "version"} <= top.keys()
    assert top["score"] == 1.0
    assert res["source"].startswith("memory:") and "embedding" in res


async def test_search_doc_types_filter(client):
    async with client:
        res = await search(client, query="예측 오차", doc_types=["reference_paper"], top_k=3)
    assert res["chunks"] and {c["doc_type"] for c in res["chunks"]} == {"reference_paper"}


async def test_search_no_result_message(client):
    async with client:
        res = await search(client, query="xyzzy", doc_types=["handover"])
    assert res["chunks"] == [] and "doc_types" in res["message"]


async def test_search_top_k_bounds(client):
    async with client:
        res = await client.call_tool("search_docs", {"query": "트럭", "top_k": 11}, raise_on_error=False)
    assert res.is_error


async def test_search_result_fits_budget(docs):
    async with Client(build_server(InMemoryKeywordStore(docs), max_chars=1500)) as c:
        res = await search(c, query="재고", top_k=10)
    assert len(json.dumps(res, ensure_ascii=False)) <= 1500 or len(res["chunks"]) == 1


# ── Resource ─────────────────────────────────────────────

async def test_resource_template_and_index(client, docs):
    async with client:
        templates = await client.list_resource_templates()
        index = json.loads((await client.read_resource("doc://index"))[0].text)
        raw = (await client.read_resource("doc://ops-rebalancing-standard"))[0].text
        frag = (await client.read_resource("doc://ops-rebalancing-standard#4"))[0].text
    assert any(t.uri_template == "doc://{doc_id}" for t in templates)
    assert {d["doc_id"] for d in index} == {d.doc_id for d in docs}
    assert raw.startswith("---") and raw == frag


async def test_unknown_resource_errors(client):
    async with client:
        with pytest.raises(Exception, match="doc://index"):
            await client.read_resource("doc://no-such-doc")
