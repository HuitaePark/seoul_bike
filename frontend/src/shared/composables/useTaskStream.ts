import { computed, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { authHeader, type Schemas } from '../api/client'

export type TaskEvent = Schemas['TaskEvent']
export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'done' | 'failed'

const MAX_BACKOFF_MS = 10_000

/**
 * 작업 진행 이벤트(SSE)를 구독한다. 계약: GET /api/ai/tasks/{taskId}/stream
 *
 * - EventSource는 Authorization 헤더를 못 보내서 fetch로 스트림을 읽는다.
 * - 연결이 끊기면 Last-Event-ID로 다시 붙어 놓친 이벤트만 받는다 (backend가 DB에서 다시 보낸다).
 * - DONE 또는 FAILED를 받으면 스트림을 닫는다.
 */
export function useTaskStream(taskId: MaybeRefOrGetter<string | null | undefined>) {
  const events = ref<TaskEvent[]>([])
  const state = ref<StreamState>('idle')
  let controller: AbortController | null = null
  let lastEventId: string | null = null
  let retryTimer: number | undefined

  const last = computed(() => events.value.at(-1))
  const answer = computed(() => {
    const done = events.value.find((e) => e.type === 'DONE')
    return done?.answer ?? events.value.filter((e) => e.type === 'TOKEN').map((e) => e.token ?? '').join('')
  })
  /** 지금 실행 중인 도구. TOOL_END가 오면 비운다. */
  const activeTool = computed(() => {
    const e = [...events.value].reverse().find((x) => x.type === 'TOOL_START' || x.type === 'TOOL_END')
    return e?.type === 'TOOL_START' ? e.tool : undefined
  })
  /** 마지막 이벤트가 승인 요청이면 승인 카드를 보여준다. 결정 뒤 새 이벤트가 오면 사라진다. */
  const pendingApproval = computed(() => (last.value?.type === 'APPROVAL_REQUIRED' ? last.value.approval : undefined))
  const sources = computed(() => events.value.find((e) => e.type === 'DONE')?.sources ?? [])
  const error = computed(() => events.value.find((e) => e.type === 'FAILED')?.error)

  function stop() {
    window.clearTimeout(retryTimer)
    controller?.abort()
    controller = null
  }

  async function connect(id: string, attempt = 0) {
    stop()
    controller = new AbortController()
    state.value = attempt === 0 ? 'connecting' : 'reconnecting'
    try {
      const res = await fetch(`/api/ai/tasks/${id}/stream`, {
        headers: {
          Accept: 'text/event-stream',
          ...authHeader(),
          ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
        },
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        state.value = 'failed'
        return
      }
      state.value = 'open'
      attempt = 0
      if (await read(res.body)) return
    } catch (e) {
      if ((e as Error).name === 'AbortError') return
    }
    // 끝나지 않았는데 끊겼다: 조금씩 늦추며 다시 붙는다.
    const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS)
    state.value = 'reconnecting'
    retryTimer = window.setTimeout(() => connect(id, attempt + 1), delay)
  }

  /** 스트림을 끝까지 읽는다. DONE·FAILED를 받았으면 true. */
  async function read(body: ReadableStream<Uint8Array>): Promise<boolean> {
    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return false
      buffer += decoder.decode(value, { stream: true })
      const blocks = buffer.split(/\r?\n\r?\n/)
      buffer = blocks.pop() ?? ''
      for (const block of blocks) {
        const event = parse(block)
        if (!event) continue
        events.value.push(event)
        lastEventId = String(event.seq)
        if (event.type === 'DONE' || event.type === 'FAILED') {
          state.value = 'done'
          reader.cancel().catch(() => {})
          return true
        }
      }
    }
  }

  function parse(block: string): TaskEvent | null {
    let data = ''
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith(':')) continue // heartbeat
      if (line.startsWith('data:')) data += line.slice(5).trim()
    }
    return data ? (JSON.parse(data) as TaskEvent) : null
  }

  watch(
    () => toValue(taskId),
    (id) => {
      stop()
      events.value = []
      lastEventId = null
      state.value = 'idle'
      if (id) connect(id)
    },
    { immediate: true },
  )
  onScopeDispose(stop)

  return { events, state, answer, activeTool, pendingApproval, sources, error, close: stop }
}
