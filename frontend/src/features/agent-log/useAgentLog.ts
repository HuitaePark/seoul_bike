import { onScopeDispose, ref } from 'vue'
import { api, authHeader, problemMessage } from '../../shared/api/client'
import { splitBlocks } from '../../shared/composables/sse'
import { MAX_LINES, mergeTraces, parseTraceBlock, type AgentTrace } from './agentLog'

export type LiveState = 'connecting' | 'live' | 'reconnecting' | 'failed'

const HISTORY = 300
const MAX_BACKOFF_MS = 10_000
/** backend는 15초마다 `: ping`을 보낸다. 이만큼 조용하면 끊긴 것으로 보고 다시 붙는다 (작업 SSE와 같은 값) */
const IDLE_MS = 40_000

/**
 * 관리자 추론 로그. 지난 기록은 목록 API로, 새 기록은 SSE로 받는다 (GET /api/admin/agent-traces, /stream).
 *
 * - EventSource는 Authorization 헤더를 못 보내 fetch로 스트림을 읽는다 (useTaskStream과 같다).
 * - 끊기면 Last-Event-ID(마지막 기록 id)로 다시 붙어 놓친 기록만 받는다. 권한 오류(4xx)는 다시 해도 같아 멈춘다.
 * - 멈춤(pause) 중에 온 기록은 모아 두었다가 이어 보기를 누르면 붙인다. 연결은 끊지 않는다.
 */
export function useAgentLog() {
  const traces = ref<AgentTrace[]>([])
  const state = ref<LiveState>('connecting')
  const error = ref('')
  const hasOlder = ref(false)
  const paused = ref(false)
  const held = ref<AgentTrace[]>([])

  let lastId: number | null = null
  let controller: AbortController | null = null
  let retryTimer: number | undefined
  let idleTimer: number | undefined
  let closed = false
  let stalled = false

  function add(incoming: AgentTrace[], cap = true) {
    if (!incoming.length) return
    traces.value = mergeTraces(traces.value, incoming, cap ? MAX_LINES : Number.MAX_SAFE_INTEGER)
    const newest = incoming.reduce((m, t) => Math.max(m, t.id), 0)
    lastId = Math.max(lastId ?? 0, newest)
  }

  async function page(query: { taskId?: string; before?: number; limit?: number }) {
    const { data, error: e } = await api.GET('/api/admin/agent-traces', { params: { query } })
    if (e || !data) {
      error.value = problemMessage(e)
      return null
    }
    error.value = ''
    return data
  }

  /** 처음: 가장 최근 기록을 읽고 그 뒤부터 실시간으로 따라간다. */
  async function start() {
    const data = await page({ limit: HISTORY })
    if (data) {
      add(data.items)
      hasOlder.value = data.hasMore
      lastId ??= 0
    }
    void connect(0)
  }

  /** 지금 보이는 가장 오래된 기록보다 앞선 것을 더 읽는다. 작업을 골랐으면 그 작업 것만. */
  async function loadOlder(taskId: string | null) {
    const shown = taskId ? traces.value.filter((t) => t.taskId === taskId) : traces.value
    const before = shown.length ? shown[0]!.id : undefined
    const data = await page({ taskId: taskId ?? undefined, before, limit: HISTORY })
    if (!data) return
    add(data.items, false)
    if (!taskId) hasOlder.value = data.hasMore
  }

  /** 작업 하나를 고르면 그 작업의 기록을 처음부터 읽는다 (실시간 창 밖의 지난 작업도 보이게). */
  async function loadTask(taskId: string) {
    const data = await page({ taskId, limit: 500 })
    if (data) add(data.items, false)
  }

  function pause() {
    paused.value = true
  }

  function follow() {
    paused.value = false
    add(held.value)
    held.value = []
  }

  function clearHeld() {
    held.value = []
  }

  function watchIdle() {
    window.clearTimeout(idleTimer)
    idleTimer = window.setTimeout(() => {
      stalled = true
      controller?.abort()
    }, IDLE_MS)
  }

  async function connect(attempt: number) {
    if (closed) return
    controller = new AbortController()
    stalled = false
    state.value = attempt === 0 ? 'connecting' : 'reconnecting'
    watchIdle()
    try {
      const res = await fetch('/api/admin/agent-traces/stream', {
        headers: {
          Accept: 'text/event-stream',
          ...authHeader(),
          ...(lastId !== null ? { 'Last-Event-ID': String(lastId) } : {}),
        },
        signal: controller.signal,
      })
      if (res.ok && res.body) {
        state.value = 'live'
        attempt = 0
        await read(res.body)
      } else if (res.status < 500) {
        state.value = 'failed'
        error.value = res.status === 403 ? '관리자만 볼 수 있다' : `실시간 기록을 열지 못했다 (${res.status})`
        return
      }
    } catch (e) {
      if ((e as Error).name === 'AbortError' && !stalled) return
    } finally {
      window.clearTimeout(idleTimer)
    }
    if (closed) return
    state.value = 'reconnecting'
    retryTimer = window.setTimeout(() => connect(attempt + 1), Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS))
  }

  async function read(body: ReadableStream<Uint8Array>) {
    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done || closed) return
      watchIdle()
      buffer += decoder.decode(value, { stream: true })
      const { blocks, rest } = splitBlocks(buffer)
      buffer = rest
      const incoming = blocks.map(parseTraceBlock).filter((t): t is AgentTrace => t !== null)
      if (!incoming.length) continue
      if (paused.value) {
        held.value = [...held.value, ...incoming]
        lastId = Math.max(lastId ?? 0, ...incoming.map((t) => t.id))
      } else {
        add(incoming)
      }
    }
  }

  function close() {
    closed = true
    window.clearTimeout(retryTimer)
    window.clearTimeout(idleTimer)
    controller?.abort()
  }

  onScopeDispose(close)

  return { traces, state, error, hasOlder, paused, held, start, loadOlder, loadTask, pause, follow, clearHeld, close }
}
