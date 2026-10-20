# Author: 정유은
# Date: 20OCT2026
# Purpose: 근거 문서(front matter + Markdown)를 읽고 절 단위로 나눈다. rag-indexer의 Chunking 단계와 같은 규칙.

"""근거 문서 읽기와 Chunking (docs/MCP.md 4.3).

rag-indexer의 첫 단계와 같은 규칙을 쓴다.
- `##` 절 단위로 자르고, 절의 `출처:` 줄을 그 절 chunk에 함께 넣는다.
- 긴 절의 표는 행 단위로 나누되, 표 머리 줄을 각 행 chunk에 붙인다.
- `[보조]` 문단(또는 제목에 `[보조]`가 있는 절, reference_paper 문서)은 basis=supplementary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DOC_TYPES = ("ops_standard", "seoul_notice", "reverse_analysis", "handover", "reference_paper")
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
    extra: dict[str, Any] = field(default_factory=dict)


def parse_doc(path: Path) -> Doc:
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: front matter가 없습니다")
    meta = yaml.safe_load(m.group(1))
    for key in ("doc_id", "doc_type", "title", "version", "verified_at", "sources"):
        if key not in meta:
            raise ValueError(f"{path.name}: front matter에 {key}가 없습니다")
    if meta["doc_type"] not in DOC_TYPES:
        raise ValueError(f"{path.name}: doc_type '{meta['doc_type']}'은 {DOC_TYPES} 중 하나여야 합니다")
    if meta["doc_id"] != path.stem:
        raise ValueError(f"{path.name}: doc_id '{meta['doc_id']}'가 파일 이름과 다릅니다")
    return Doc(doc_id=meta["doc_id"], meta=meta, raw=raw, body=m.group(2))


def load_docs(docs_dir: Path) -> list[Doc]:
    return [parse_doc(p) for p in sorted(docs_dir.glob("*.md"))]


def _format_source(src: dict[str, Any]) -> str:
    if src.get("citation"):
        return src["citation"]
    if src.get("type") == "github":
        paths = ", ".join(src.get("paths", []))
        return f"{src.get('repo')}@{src.get('commit')} {paths}".strip()
    return src.get("doc_id", "출처 미상")


def citations(doc: Doc) -> tuple[str, str]:
    """(기본 근거 출처, 보조 근거 출처)."""
    primary = [_format_source(s) for s in doc.meta["sources"] if s.get("role") == "primary"]
    supplementary = [_format_source(s) for s in doc.meta["sources"] if s.get("role") == "supplementary"]
    return "; ".join(primary) or doc.title, "; ".join(supplementary) or "보조 자료"


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
    primary_cite, supp_cite = citations(doc)
    whole_doc_supp = doc.doc_type == "reference_paper"
    chunks: list[Chunk] = []

    def add(section: str, body: str, tail: str, basis: str, section_no: str) -> None:
        if not body.strip():
            return
        text = (body.strip() + tail).strip()
        citation = primary_cite if (basis == "primary" or whole_doc_supp) else supp_cite
        chunks.append(Chunk(
            chunk_id=f"{doc.doc_id}#{len(chunks) + 1}", doc_id=doc.doc_id, doc_type=doc.doc_type,
            title=doc.title, section=section, text=text, basis=basis, citation=citation,
            data_period=doc.meta.get("data_period"), version=int(doc.meta["version"]),
            uri=f"doc://{doc.doc_id}#{section_no}",
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
