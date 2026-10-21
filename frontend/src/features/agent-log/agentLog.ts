// 에이전트 추론 로그(관리자) 화면의 규칙. fetch·Vue와 떼어 두어 node --test로 시험한다.
// 계약: openapi v0.16 GET /api/admin/agent-traces, /stream (AgentTrace). 값은 agent가 마스킹한 것이다.

import type { Schemas } from '../../shared/api/client'

export type AgentTrace = Schemas['AgentTrace']
export type TraceKind = Schemas['AgentTraceKind']

/** 줄 머리의 종류 이름. 판단(DECISION)이 이 화면의 중심이다: 모델이 무엇을 왜 골랐는가 */
export const kindLabel: Record<TraceKind, string> = {
  RUN_START: '시작',
  ROUTE: '경로',
  DECISION: '판단',
  GUARD: '안전장치',
  TOOL_CALL: '도구 호출',
  TOOL_RESULT: '도구 결과',
  RECOVERY: '복구',
  APPROVAL_WAIT: '승인 대기',
  RUN_END: '끝',
}

export const sourceLabel: Record<AgentTrace['source'], string> = { HARNESS: '모델', CODE: '규칙', GRAPH: '그래프' }

/** 거르기 묶음. 화면의 칩과 명령(`kind ...`)이 같은 이름을 쓴다 */
export const kindGroups = {
  decision: ['DECISION'],
  tool: ['TOOL_CALL', 'TOOL_RESULT'],
  guard: ['GUARD', 'RECOVERY'],
  approval: ['APPROVAL_WAIT'],
  flow: ['RUN_START', 'RUN_END', 'ROUTE'],
} as const satisfies Record<string, readonly TraceKind[]>
export type KindGroup = keyof typeof kindGroups
export const groupLabel: Record<KindGroup, string> = {
  decision: '판단',
  tool: '도구',
  guard: '안전장치·복구',
  approval: '승인',
  flow: '시작·끝·경로',
}
export const ALL_GROUPS = Object.keys(kindGroups) as KindGroup[]

export type Filter = { taskId: string | null; groups: KindGroup[]; text: string }
export const emptyFilter = (): Filter => ({ taskId: null, groups: [...ALL_GROUPS], text: '' })

/** 줄 수 한도. 넘으면 오래된 줄부터 버린다 (브라우저 메모리). 지난 기록은 목록 API로 다시 읽는다 */
export const MAX_LINES = 3000

/** 새 기록을 합친다. 같은 id는 한 번만, id(받은 순서) 순으로, 한도를 넘으면 앞에서 자른다. */
export function mergeTraces(current: AgentTrace[], incoming: AgentTrace[], max = MAX_LINES): AgentTrace[] {
  if (!incoming.length) return current
  const byId = new Map(current.map((t) => [t.id, t]))
  for (const t of incoming) byId.set(t.id, t)
  const merged = [...byId.values()].sort((a, b) => a.id - b.id)
  return merged.length > max ? merged.slice(merged.length - max) : merged
}

/** 판단 줄의 도구마다 모델이 적은 이유. 이유가 없는 호출(스크립트 모델 등)은 빠진다 */
export function rationales(trace: AgentTrace): { name: string; rationale: string }[] {
  if (trace.kind !== 'DECISION') return []
  return (trace.detail.calls ?? [])
    .filter((c) => c.rationale)
    .map((c) => ({ name: c.name ?? '', rationale: c.rationale as string }))
}

function haystack(trace: AgentTrace): string {
  const reasons = rationales(trace).map((r) => `${r.name} ${r.rationale}`)
  return [trace.title, trace.taskId, trace.shiftId, ...reasons, ...(trace.detail.reasons ?? [])].join(' ').toLowerCase()
}

export function visible(traces: AgentTrace[], filter: Filter): AgentTrace[] {
  const kinds = new Set<TraceKind>(filter.groups.flatMap((g) => kindGroups[g]))
  const text = filter.text.trim().toLowerCase()
  return traces.filter(
    (t) =>
      (!filter.taskId || t.taskId === filter.taskId) &&
      kinds.has(t.kind) &&
      (!text || haystack(t).includes(text)),
  )
}

export type TaskSummary = {
  taskId: string
  shiftId: string
  taskType: string | null
  /** 마지막으로 알려진 상태: 실행 중, 승인 대기, 완료, 실패 */
  status: 'RUNNING' | 'WAITING_APPROVAL' | 'DONE' | 'FAILED'
  lastTitle: string
  lastAt: string
  lines: number
  decisions: number
}

/** 로그에 보인 작업 목록 (최근 것 먼저). 상태는 그 작업의 마지막 끝(RUN_END)·승인 대기 줄로 정한다 */
export function summarize(traces: AgentTrace[]): TaskSummary[] {
  const tasks = new Map<string, TaskSummary>()
  for (const t of traces) {
    const s = tasks.get(t.taskId) ?? {
      taskId: t.taskId, shiftId: t.shiftId, taskType: null, status: 'RUNNING' as const, lastTitle: '', lastAt: '',
      lines: 0, decisions: 0,
    }
    s.lines += 1
    if (t.kind === 'DECISION') s.decisions += 1
    if (t.kind === 'RUN_START' && t.detail.taskType) s.taskType = t.detail.taskType
    if (t.kind === 'ROUTE' && !s.taskType && typeof t.detail.command === 'string') s.taskType = t.detail.command
    if (t.kind === 'RUN_START') s.status = 'RUNNING'
    if (t.kind === 'APPROVAL_WAIT') s.status = 'WAITING_APPROVAL'
    if (t.kind === 'RUN_END') {
      const status = t.detail.status
      s.status = status === 'DONE' ? 'DONE' : status === 'FAILED' ? 'FAILED' : 'WAITING_APPROVAL'
    }
    s.lastTitle = t.title
    s.lastAt = t.occurredAt
    tasks.set(t.taskId, s)
  }
  return [...tasks.values()].sort((a, b) => b.lastAt.localeCompare(a.lastAt))
}

export const shortId = (id: string) => id.slice(0, 8)

/** 화면 시각 HH:MM:SS (브라우저 시간대). 칸 폭이 같도록 숫자로만 쓴다. 읽을 수 없으면 그대로 */
export function clock(iso: string): string {
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return [d.getHours(), d.getMinutes(), d.getSeconds()].map((n) => String(n).padStart(2, '0')).join(':')
}

/** 결과가 오류였거나 안전장치가 막았거나 실패로 끝난 줄: 붉게 보인다 */
export function isProblem(trace: AgentTrace): boolean {
  return (
    (trace.kind === 'TOOL_RESULT' && trace.detail.ok === false) ||
    (trace.kind === 'GUARD' && trace.detail.action === 'FAIL') ||
    (trace.kind === 'RUN_END' && trace.detail.status === 'FAILED')
  )
}

/**
 * SSE 블록 하나 → 추론 기록. 주석(`: ping`)만 있거나 data가 없으면 null.
 * 작업 SSE의 parseBlock(TaskEvent)과 같은 규칙이고, id는 data 안의 `id`를 쓴다.
 */
export function parseTraceBlock(block: string): AgentTrace | null {
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  if (!data.length) return null
  try {
    const trace = JSON.parse(data.join('\n')) as AgentTrace
    return typeof trace.id === 'number' && trace.kind ? trace : null
  } catch {
    return null
  }
}

/** 화면 아래 명령 줄. 버튼과 같은 일을 글로 한다 (CLI `python -m graph.agentlog`와 같은 이름) */
export type Command =
  | { type: 'task'; taskId: string | null }
  | { type: 'kind'; groups: KindGroup[] }
  | { type: 'grep'; text: string }
  | { type: 'pause' }
  | { type: 'follow' }
  | { type: 'clear' }
  | { type: 'more' }
  | { type: 'verbose'; on: boolean }
  | { type: 'help' }
  | { type: 'error'; message: string }

export const commandHelp = [
  'task <작업 ID 앞부분> | task all   — 한 작업만 보기 / 모두 보기',
  'kind decision,tool,guard,approval,flow | kind all   — 줄 종류 거르기',
  'grep <글자> | grep   — 제목·이유에 글자가 있는 줄만 / 해제',
  'pause | follow   — 실시간 따라가기 멈춤 / 다시',
  'more   — 지난 기록 더 읽기',
  'verbose on|off   — 인자·결과 미리보기 펼치기',
  'clear   — 화면 비우기 (기록은 남는다)',
]

/** 명령 한 줄을 읽는다. taskId 앞부분은 아는 작업 ID 중 하나로 풀어 준다 */
export function parseCommand(input: string, knownTaskIds: string[]): Command {
  const [word = '', ...rest] = input.trim().split(/\s+/)
  const arg = rest.join(' ').trim()
  switch (word.toLowerCase()) {
    case 'task': {
      if (!arg || arg === 'all') return { type: 'task', taskId: null }
      const found = knownTaskIds.filter((id) => id.startsWith(arg))
      if (found.length === 1) return { type: 'task', taskId: found[0] }
      if (/^[0-9a-f-]{36}$/i.test(arg)) return { type: 'task', taskId: arg }
      return { type: 'error', message: found.length ? `'${arg}'로 시작하는 작업이 여럿이다` : `'${arg}' 작업을 로그에서 찾지 못했다` }
    }
    case 'kind': {
      if (!arg || arg === 'all') return { type: 'kind', groups: [...ALL_GROUPS] }
      const groups = arg.split(/[,\s]+/).filter(Boolean)
      const unknown = groups.filter((g) => !(g in kindGroups))
      if (unknown.length) return { type: 'error', message: `모르는 종류: ${unknown.join(', ')} (decision, tool, guard, approval, flow)` }
      return { type: 'kind', groups: groups as KindGroup[] }
    }
    case 'grep':
      return { type: 'grep', text: arg }
    case 'pause':
      return { type: 'pause' }
    case 'follow':
    case 'resume':
      return { type: 'follow' }
    case 'clear':
      return { type: 'clear' }
    case 'more':
      return { type: 'more' }
    case 'verbose':
      return { type: 'verbose', on: arg !== 'off' }
    case 'help':
    case '?':
      return { type: 'help' }
    default:
      return { type: 'error', message: `모르는 명령: ${word}. help로 목록을 본다` }
  }
}
