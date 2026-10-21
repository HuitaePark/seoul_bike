# Author: 정유은
# Date: 20OCT2026
# Purpose: 근거 문서(front matter + Markdown)를 읽고 절 단위로 나눈다. rag-indexer의 Chunking 단계와 같은 규칙.

"""근거 문서 읽기와 Chunking (docs/MCP.md 4.3).

머리말은 팀 합의(docs/data/agreements.md 21번)를 따른다: `title`, `doc_id`, `version`, `updated`, `source`, `status`.
MCP가 더 쓰는 선택 필드: `doc_type`(검색 거르기), `sources[]`(기본·보조 근거 구분), `data_period`.

rag-indexer의 첫 단계와 같은 규칙을 쓴다.
- `##` 절 단위로 자르고, 절의 `출처:` 줄을 그 절 chunk에 함께 넣는다.
- 긴 절의 표는 행 단위로 나누되, 표 머리 줄을 각 행 chunk에 붙인다.
- `[보조]` 문단(또는 제목에 `[보조]`가 있는 절, reference_paper 문서)은 basis=supplementary.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DOC_TYPES = ("ops_standard", "seoul_notice", "reverse_analysis", "handover", "reference_paper", "general")
DEFAULT_DOC_TYPE = "general"
REQUIRED_KEYS = ("doc_id", "title", "version")
MAX_SECTION_CHARS = 1200
SUPPLEMENTARY_MARK = "[보조]"


@dataclass(frozen=True)
class Doc:
    doc_id: str
    meta: dict[str, Any]
    raw: str  # front matter 포함 원문 (Resource doc://{doc_id})
    body: str

    @property
    def doc_type(self) -> str:
        return self.meta["doc_type"]

    @property
    def title(self) -> str:
        return self.meta["title"]

    @property
    def updated(self) -> str:
        return str(self.meta.get("updated", ""))

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.raw.encode("utf-8")).hexdigest()


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_type: str
    title: str
    section: str
    text: str
    basis: str
    citation: str
    data_period: str | None
    version: int
    uri: str
    status: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def embed_text(self) -> str:
        """임베딩에 넣는 글. 제목·절 이름을 앞에 붙여 절 하나만 읽혀도 뜻이 통하게 한다."""
        return f"{self.title}\n{self.section}\n{self.text}"


def parse_doc(path: Path) -> Doc:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: front matter가 없습니다")
    meta = yaml.safe_load(m.group(1)) or {}
    for key in REQUIRED_KEYS:
        if key not in meta:
            raise ValueError(f"{path.name}: front matter에 {key}가 없습니다")
    if meta["doc_id"] != path.stem:
        raise ValueError(f"{path.name}: doc_id '{meta['doc_id']}'가 파일 이름과 다릅니다")
    # 예전 필드 이름(verified_at)도 받는다.
    if "updated" not in meta and "verified_at" in meta:
        meta["updated"] = meta["verified_at"]
    meta.setdefault("doc_type", DEFAULT_DOC_TYPE)
    if meta["doc_type"] not in DOC_TYPES:
        raise ValueError(f"{path.name}: doc_type '{meta['doc_type']}'은 {DOC_TYPES} 중 하나여야 합니다")
    # 합의 21번 머리말은 source 한 줄이다. sources[]가 없으면 그것을 기본 근거로 쓴다.
    if not meta.get("sources"):
        meta["sources"] = [{"role": "primary", "type": "document", "citation": meta.get("source") or meta["title"]}]
    return Doc(doc_id=meta["doc_id"], meta=meta, raw=raw, body=m.group(2))


def load_docs(docs_dir: Path) -> list[Doc]:
    docs = [parse_doc(p) for p in sorted(docs_dir.glob("*.md")) if p.name.lower() != "readme.md"]
    ids = [d.doc_id for d in docs]
    if len(ids) != len(set(ids)):
        raise ValueError(f"doc_id가 겹칩니다: {sorted({i for i in ids if ids.count(i) > 1})}")
    return docs


def _format_source(src: dict[str, Any]) -> str:
    if src.get("citation"):
        return src["citation"]
    if src.get("type") == "github":
        paths = ", ".join(src.get("paths", []))
        return f"{src.get('repo')}@{src.get('commit')} {paths}".strip()
    return src.get("doc_id", "출처 미상")


def citations(doc: Doc) -> tuple[str, str]:
    """(기본 근거 출처, 보조 근거 출처 전부)."""
    primary = [_format_source(s) for s in doc.meta["sources"] if s.get("role") == "primary"]
    supplementary = [_format_source(s) for s in doc.meta["sources"] if s.get("role") == "supplementary"]
    return "; ".join(primary) or doc.title, "; ".join(supplementary) or "보조 자료"


def _source_keys(src: dict[str, Any]) -> tuple[list[str], list[str]]:
    """보조 출처를 알아볼 낱말. (구체적인 것: doc_id·URL 끝 번호, 일반적인 것: 출처 글의 첫 낱말, 예 '김유신'·'서울시')."""
    specific = [src["doc_id"]] if src.get("doc_id") else []
    if src.get("url"):
        tail = src["url"].rstrip("/").rsplit("/", 1)[-1]
        if len(tail) >= 4:
            specific.append(tail)
    first = re.split(r"[,\s「」]+", (src.get("citation") or "").strip())
    general = [first[0]] if first and len(first[0]) >= 2 else []
    return specific, general


def _mentions(text: str, keys: list[str]) -> bool:
    # 이름 바로 뒤에 한글이 이어지면 다른 낱말이다 (예 '서울시설공단'은 '서울시'가 아니다).
    return any(re.search(re.escape(k) + r"(?![가-힣])", text) for k in keys)


def supplementary_citation(doc: Doc, text: str) -> str:
    """[보조] 조각의 출처. 본문·출처 줄에 표시가 나오는 보조 출처만 고른다.

    doc_id·URL 번호처럼 구체적인 표시가 맞는 출처가 있으면 그것만, 없으면 저자·기관 이름이 맞는 출처, 그것도 없으면 전부.
    """
    sources = [s for s in doc.meta["sources"] if s.get("role") == "supplementary"]
    specific = [s for s in sources if _mentions(text, _source_keys(s)[0])]
    general = [s for s in sources if _mentions(text, _source_keys(s)[1])]
    return "; ".join(_format_source(s) for s in (specific or general or sources)) or "보조 자료"


def _split_sections(body: str) -> list[tuple[str, list[str]]]:
    """('## ' 제목, 줄 목록) 목록. 첫 '## ' 앞의 머리말은 '개요' 절이 된다. 코드 블록 안의 '##'는 무시한다."""
    sections: list[tuple[str, list[str]]] = [("개요", [])]
    in_fence = False
    for line in body.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        if not in_fence and line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif not in_fence and line.startswith("# "):
            continue  # 문서 제목은 title 필드에 이미 있다
        else:
            sections[-1][1].append(line)
    return [(h, lines) for h, lines in sections if "".join(lines).strip()]


def _is_supplementary_line(line: str) -> bool:
    s = line.lstrip("- ").lstrip()
    return s.startswith(f"`{SUPPLEMENTARY_MARK}`") or s.startswith(SUPPLEMENTARY_MARK)


def _table_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    """줄을 ('text', 줄들) / ('table', 줄들) 블록으로 나눈다. 코드 블록 안의 표는 text로 둔다."""
    blocks: list[tuple[str, list[str]]] = []
    in_fence = False
    for line in lines:
        if line.startswith("```"):
            in_fence = not in_fence
        kind = "table" if (not in_fence and line.startswith("|")) else "text"
        if blocks and blocks[-1][0] == kind:
            blocks[-1][1].append(line)
        else:
            blocks.append((kind, [line]))
    return blocks


def chunk_doc(doc: Doc) -> list[Chunk]:
    primary_cite, _ = citations(doc)
    whole_doc_supp = doc.doc_type == "reference_paper"
    chunks: list[Chunk] = []

    def add(section: str, body: str, tail: str, basis: str, section_no: str) -> None:
        if not body.strip():
            return
        text = (body.strip() + tail).strip()
        citation = primary_cite if (basis == "primary" or whole_doc_supp) else supplementary_citation(doc, text)
        chunks.append(Chunk(
            chunk_id=f"{doc.doc_id}#{len(chunks) + 1}", doc_id=doc.doc_id, doc_type=doc.doc_type,
            title=doc.title, section=section, text=text, basis=basis, citation=citation,
            data_period=doc.meta.get("data_period"), version=int(doc.meta["version"]),
            uri=f"doc://{doc.doc_id}#{section_no}", status=doc.meta.get("status"),
        ))

    for idx, (heading, lines) in enumerate(_split_sections(doc.body)):
        m = re.match(r"^(\d+)\.", heading)
        section_no = m.group(1) if m else str(idx)
        source_lines = [l for l in lines if l.startswith("출처:")]
        source_tail = "\n\n" + "\n".join(source_lines) if source_lines else ""
        section_supp = whole_doc_supp or SUPPLEMENTARY_MARK in heading
        base_basis = "supplementary" if section_supp else "primary"

        main: list[str] = []
        for line in lines:
            if line.startswith("출처:"):
                continue
            if not section_supp and _is_supplementary_line(line):
                add(heading, line, source_tail, "supplementary", section_no)
            else:
                main.append(line)

        main_text = "\n".join(main)
        if len(main_text) <= MAX_SECTION_CHARS:
            add(heading, main_text, source_tail, base_basis, section_no)
            continue
        for kind, block in _table_blocks(main):
            if kind == "text" or len(block) <= 3:
                add(heading, "\n".join(block), source_tail, base_basis, section_no)
                continue
            header = block[:2]
            for row in block[2:]:
                add(heading, "\n".join([*header, row]), source_tail, base_basis, section_no)
    return chunks
