// 근거 패널 (ROLES 3): 작업 DONE의 sources(openapi Source)를 화면에 보일 모양으로 나눈다.
// 계약 Source는 kind·title·uri·excerpt만 있다. citation·basis·원문 보기는 계약에 들어온 뒤 붙인다.

import type { Schemas } from '../../shared/api/client'

export type Source = Schemas['Source']

/** 문서 근거 한 건. rag-mcp chunk에서 온다 (그래프 doc_sources: 제목 "문서 · 절", uri doc://{docId}#{절}). */
export interface DocEvidence {
  key: string
  docId: string | null
  docTitle: string
  section: string | null
  excerpt: string | null
}

/** 도구 근거 한 건. 답에 쓴 업무 도구 결과 (예: "위험 대여소 조회"). */
export interface ToolEvidence {
  key: string
  label: string
}

const DOC_SCHEME = 'doc://'
const TITLE_SEPARATOR = ' · '

/** doc://{docId}#{절}에서 docId와 절을 꺼낸다. 형식이 다르면 null. */
export function parseDocUri(uri: string | null | undefined): { docId: string; section: string | null } | null {
  if (!uri || !uri.startsWith(DOC_SCHEME)) return null
  const rest = uri.slice(DOC_SCHEME.length)
  const hash = rest.indexOf('#')
  const docId = (hash < 0 ? rest : rest.slice(0, hash)).trim()
  if (!docId) return null
  let section: string | null = null
  if (hash >= 0) {
    try {
      section = decodeURIComponent(rest.slice(hash + 1)).trim() || null
    } catch {
      // 잘못 인코딩된 절 이름은 원문 그대로 둔다
      section = rest.slice(hash + 1).trim() || null
    }
  }
  return { docId, section }
}

/** "문서 제목 · 절" 제목을 나눈다. 구분자가 없으면 절은 uri에서 찾는다. */
function splitTitle(title: string, uriSection: string | null): { docTitle: string; section: string | null } {
  const at = title.lastIndexOf(TITLE_SEPARATOR)
  if (at > 0) return { docTitle: title.slice(0, at).trim(), section: title.slice(at + TITLE_SEPARATOR.length).trim() || uriSection }
  return { docTitle: title.trim(), section: uriSection }
}

/** sources를 문서·도구로 나누고 같은 근거는 한 번만 남긴다. 받은 순서를 지킨다. */
export function groupSources(sources: readonly Source[] | null | undefined): { docs: DocEvidence[]; tools: ToolEvidence[] } {
  const docs: DocEvidence[] = []
  const tools: ToolEvidence[] = []
  const seen = new Set<string>()

  for (const s of sources ?? []) {
    // 계약상 필수인 title이 비면 보일 것이 없다
    if (!s || !s.title?.trim()) continue

    if (s.kind === 'DOC') {
      const key = `DOC:${s.uri ?? s.title}`
      if (seen.has(key)) continue
      seen.add(key)
      const parsed = parseDocUri(s.uri)
      const { docTitle, section } = splitTitle(s.title, parsed?.section ?? null)
      docs.push({ key, docId: parsed?.docId ?? null, docTitle, section, excerpt: s.excerpt?.trim() || null })
    } else {
      const key = `TOOL:${s.title}`
      if (seen.has(key)) continue
      seen.add(key)
      tools.push({ key, label: s.title.trim() })
    }
  }
  return { docs, tools }
}
