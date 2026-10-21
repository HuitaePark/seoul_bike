# Author: 정유은
# Date: 20OCT2026
# Purpose: rag-mcp 도구·Resource·색인기 테스트. 저장소의 docs/evidence 문서를 인메모리 Client로 검색하고, pgvector는 실제 DB로 확인한다.

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastmcp import Client

from rag_mcp.docs import chunk_doc, load_docs, parse_doc
from rag_mcp.embedding import HashEmbedder
from rag_mcp.server import build_server
from rag_mcp.store import InMemoryKeywordStore

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs" / "evidence"
AGREEMENT_HEAD = ("title", "doc_id", "version", "updated", "source", "status")  # docs/data/agreements.md 21번

REBAL_LIKE = """---
title: 따릉이 트럭 재배치 역산 요약
doc_id: rebal-summary
version: 1
updated: 2026-10-20
source: 「따릉이 재배치 역산 정리」(2026-10-20), 서울 열린데이터광장 공공자전거 대여이력 2024~2025
status: 추정치. 공개 데이터에 재배치 기록이 없어 대여이력에서 거꾸로 추정했다
---

# 따릉이 트럭 재배치 역산 요약

## 트럭 용량과 한 번에 옮기는 대수

따릉이 재배치 트럭 한 대의 용량은 약 15대로 추정한다.
"""


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

def test_evidence_docs_follow_agreement_head(docs):
    """합의 21번 머리말 6개를 모두 갖고, 그 순서로 시작한다."""
    assert {d.doc_id for d in docs} == {"ops-rebalancing-standard", "notice-seoul-fireworks-2025",
                                         "handover-shift-summary-template", "ref-forecast-kim-2023",
                                         "rebal-summary"}
    for d in docs:
        assert list(d.meta)[: len(AGREEMENT_HEAD)] == list(AGREEMENT_HEAD), d.doc_id
        assert any(s["role"] == "primary" for s in d.meta["sources"])


def test_agreement_head_only_doc_is_readable(tmp_path):
    """6번 rebal-summary처럼 합의 머리말만 있는 문서: source가 기본 근거, doc_type은 알려진 표로 정한다."""
    p = tmp_path / "rebal-summary.md"
    p.write_text(REBAL_LIKE, encoding="utf-8")
    doc = parse_doc(p)
    assert doc.doc_type == "reverse_analysis" and doc.updated == "2026-10-20"
    chunks = chunk_doc(doc)
    assert chunks and all(c.basis == "primary" for c in chunks)
    assert chunks[-1].citation.startswith("「따릉이 재배치 역산 정리」") and chunks[-1].status.startswith("추정치")


def test_doc_id_must_match_file_name(tmp_path):
    p = tmp_path / "other-name.md"
    p.write_text(REBAL_LIKE, encoding="utf-8")
    with pytest.raises(ValueError, match="파일 이름"):
        parse_doc(p)


def test_supplementary_paragraphs_are_split_out(docs):
    ops = next(d for d in docs if d.doc_id == "ops-rebalancing-standard")
    chunks = chunk_doc(ops)
    supp = [c for c in chunks if c.basis == "supplementary"]
    assert supp and all("[보조]" in c.text for c in supp)
    assert all("김유신" in c.citation or "서울시" in c.citation for c in supp)
    # 서울시 보도자료에서 온 문단에는 논문 출처를 붙이지 않는다.
    fireworks = next(c for c in supp if "9월 26일 19시" in c.text)
    assert "서울시" in fireworks.citation and "김유신" not in fireworks.citation
    rmse = next(c for c in supp if "RMSE" in c.text)
    assert "김유신" in rmse.citation and "서울시" not in rmse.citation
    staff = next(c for c in supp if "서울시설공단" in c.text)  # '서울시설공단'은 '서울시' 보도자료가 아니다
    assert "서울시," not in staff.citation


def test_specific_source_mark_wins(docs):
    """출처 줄의 보도자료 번호(530468)가 맞으면 같은 기관의 다른 기사(미디어허브)는 붙이지 않는다."""
    notice = next(d for d in docs if d.doc_id == "notice-seoul-fireworks-2025")
    part = next(c for c in chunk_doc(notice) if c.section.startswith("2. 중단 내용"))
    assert "종합대책" in part.citation and "미디어허브" not in part.citation


def test_reference_paper_is_supplementary_with_paper_citation(docs):
    paper = next(d for d in docs if d.doc_type == "reference_paper")
    chunks = chunk_doc(paper)
    assert {c.basis for c in chunks} == {"supplementary"}
    assert all("김유신" in c.citation for c in chunks)


def test_chunks_keep_source_line_and_stay_short(docs):
    ops = next(d for d in docs if d.doc_id == "ops-rebalancing-standard")
    for c in chunk_doc(ops):
        if c.section != "개요":
            assert "출처:" in c.text, c.chunk_id
    for d in docs:
        for c in chunk_doc(d):
            assert len(c.text) <= 1600, c.chunk_id


def test_parse_rejects_missing_front_matter(tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("# 제목만 있음\n", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_doc(p)


def test_hash_embedder_is_deterministic_and_unit_length():
    e = HashEmbedder()
    a, b = e.embed_documents(["트럭 용량 15대", "트럭 용량 15대"])
    assert a == b and abs(sum(x * x for x in a) - 1) < 1e-9 and e.dim == 256


# ── search_docs (메모리 저장소) ──────────────────────────

async def test_search_returns_chunks_with_citation_and_status(client):
    async with client:
        res = await search(client, query="불꽃축제 대여 반납 중지")
    top = res["chunks"][0]
    assert top["doc_id"] == "notice-seoul-fireworks-2025"
    assert {"citation", "basis", "section", "uri", "score", "version", "status"} <= top.keys()
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
    assert all(d["updated"] for d in index)
    assert raw.startswith("---") and raw == frag


async def test_unknown_resource_errors(client):
    async with client:
        with pytest.raises(Exception, match="doc://index"):
            await client.read_resource("doc://no-such-doc")


# ── pgvector 색인·검색 (실제 DB) ─────────────────────────

DSN = os.environ.get("RAG_TEST_DSN")
needs_pg = pytest.mark.skipif(not DSN, reason="RAG_TEST_DSN이 없으면 건너뛴다 (CI는 pgvector 서비스로 실행)")


@pytest.fixture
def pg_schema():
    """테스트마다 새 스키마를 써서 서로 섞이지 않게 한다."""
    import psycopg

    schema = f"rag_test_{uuid.uuid4().hex[:8]}"
    admin = psycopg.connect(DSN, autocommit=True)
    admin.execute("CREATE EXTENSION IF NOT EXISTS vector")
    admin.execute(f"CREATE SCHEMA {schema}")

    def connect():
        from pgvector.psycopg import register_vector

        conn = psycopg.connect(DSN, options=f"-c search_path={schema},public")
        register_vector(conn)
        return conn

    yield connect
    admin.execute(f"DROP SCHEMA {schema} CASCADE")
    admin.close()


@pytest.mark.pgvector
@needs_pg
def test_indexer_builds_records_model_and_skips_unchanged(pg_schema, docs):
    from rag_mcp.indexer import build_index, last_index

    emb = HashEmbedder()
    with pg_schema() as conn:
        first = build_index(conn, docs, emb)
        second = build_index(conn, docs, emb)
        meta = last_index(conn)
        n_chunks = conn.execute("SELECT count(*) FROM rag_chunk").fetchone()[0]
        dims = conn.execute("SELECT vector_dims(embedding) FROM rag_chunk LIMIT 1").fetchone()[0]
        hnsw = conn.execute("SELECT count(*) FROM pg_indexes WHERE indexname = 'rag_chunk_embedding_hnsw'").fetchone()[0]
    assert first["skipped"] is False and first["chunk_count"] == n_chunks == sum(len(chunk_doc(d)) for d in docs)
    assert second["skipped"] is True
    assert (meta["model"], meta["dim"], dims) == ("hash-bigram-256", 256, 256)
    assert hnsw == 1


@pytest.mark.pgvector
@needs_pg
def test_indexer_rebuilds_when_model_dimension_changes(pg_schema, docs):
    from rag_mcp.indexer import build_index

    with pg_schema() as conn:
        build_index(conn, docs, HashEmbedder(256))
        result = build_index(conn, docs, HashEmbedder(128))
        dims = conn.execute("SELECT vector_dims(embedding) FROM rag_chunk LIMIT 1").fetchone()[0]
    assert result["skipped"] is False and result["dim"] == dims == 128


@pytest.mark.pgvector
@needs_pg
async def test_pgvector_store_search_and_resources(pg_schema, docs):
    from rag_mcp.indexer import build_index
    from rag_mcp.pgvector_store import IndexMismatch, PgVectorStore

    emb = HashEmbedder()
    with pg_schema() as conn:
        build_index(conn, docs, emb)
    store = PgVectorStore(pg_schema, emb)
    with pytest.raises(IndexMismatch):
        PgVectorStore(pg_schema, HashEmbedder(128))  # 색인과 다른 모델이면 실행하지 않는다
    async with Client(build_server(store)) as c:
        res = await search(c, query="불꽃축제 대여 반납 중지 기간")
        paper = await search(c, query="예측 오차", doc_types=["reference_paper"])
        index = json.loads((await c.read_resource("doc://index"))[0].text)
        raw = (await c.read_resource("doc://notice-seoul-fireworks-2025"))[0].text
    assert res["chunks"][0]["doc_id"] == "notice-seoul-fireworks-2025"
    assert res["embedding"] == {"model": "hash-bigram-256", "dim": 256}
    assert res["source"].startswith("pgvector:")
    assert {c["doc_type"] for c in paper["chunks"]} == {"reference_paper"}
    assert {d["doc_id"] for d in index} == {d.doc_id for d in docs} and raw.startswith("---")


@pytest.mark.fastembed
@pytest.mark.skipif(os.environ.get("RAG_TEST_FASTEMBED") != "1", reason="RAG_TEST_FASTEMBED=1일 때만 모델을 받는다")
def test_fastembed_model_ranks_semantic_match():
    from rag_mcp.embedding import FastEmbedEmbedder

    e = FastEmbedEmbedder()  # 기본 모델 (intfloat/multilingual-e5-large)
    q = e.embed_query("트럭에 자전거를 몇 대까지 실을 수 있어?")
    near, far = e.embed_documents(["트럭 용량은 약 15대로 추정한다", "불꽃축제 때 대여·반납을 중지했다"])
    dot = lambda a, b: sum(x * y for x, y in zip(a, b))
    assert e.dim == 1024 and dot(q, near) > dot(q, far)
