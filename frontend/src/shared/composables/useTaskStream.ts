import { computed, onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { authHeader } from '../api/client'
import { parseBlock, splitBlocks, type TaskEvent } from './sse'

export type { TaskEvent }
export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'done' | 'failed'
export type TaskStreamHandle = { close: () => void }

const MAX_BACKOFF_MS = 10_000
/**
 * backend는 15초마다 `: ping`을 보낸다. 이만큼 아무것도 오지 않으면 연결이 조용히 죽은 것으로 보고 다시 붙는다.
 * (backend가 재시작될 때 프록시가 끊김을 알려 주지 않으면 스트림이 끝나지도 않고 멈춰 있다.)
 */
const IDLE_MS = 40_000

/**
 * 작업 하나의 진행 이벤트(SSE)를 연다. 계약: GET /api/ai/tasks/{taskId}/stream
 *
 * - EventSource는 Authorization 헤더를 못 보내서 fetch로 스트림을 읽는다.
 * - 연결이 끊기거나 IDLE_MS 동안 조용하면 Last-Event-ID로 다시 붙어 놓친 이벤트만 받는다 (backend가 DB에서 다시 보낸다).
 *   서버 쪽 일시 오류(5xx: 배포 중 교체, 프록시 502 등)도 다시 붙는다. 권한·없는 작업(4xx)은 다시 해도 같아 멈춘다.
 * - DONE 또는 FAILED를 받으면 스트림을 닫는다. close()를 부르면 그 뒤로는 아무것도 알리지 않는다.
 * - 여러 작업을 함께 볼 때(관제 대시보드)는 이것을 작업마다 연다. 한 작업만 보면 useTaskStream을 쓴다.
 */
export function openTaskStream(
  taskId: string,
  handlers: { onEvent: (event: TaskEvent) => void; onState?: (state: StreamState) => void },
): TaskStreamHandle {
  let controller: AbortController | null = null
  let lastEventId: string | null = null
  let retryTimer: number | undefined
  let idleTimer: number | undefined
  let closed = false
  let stalled = false
  const setState = (s: StreamState) => {
    if (!closed) handlers.onState?.(s)
  }

  function close() {
    closed = true
    window.clearTimeout(retryTimer)
    window.clearTimeout(idleTimer)
    controller?.abort()
    controller = null
  }

  /** 무엇이든 받을 때마다 다시 잰다. 시간이 다 되면 지금 연결을 끊고 다시 붙는다. */
  function watchIdle() {
    window.clearTimeout(idleTimer)
    idleTimer = window.setTimeout(() => {
      stalled = true
      controller?.abort()
    }, IDLE_MS)
  }

  async function connect(attempt: number) {
    controller = new AbortController()
    stalled = false
    setState(attempt === 0 ? 'connecting' : 'reconnecting')
    watchIdle()
    try {
      const res = await fetch(`/api/ai/tasks/${taskId}/stream`, {
        headers: {
          Accept: 'text/event-stream',
          ...authHeader(),
          ...(lastEventId ? { 'Last-Event-ID': lastEventId } : {}),
        },
        signal: controller.signal,
      })
      if (res.ok && res.body) {
        setState('open')
        attempt = 0
        if (await read(res.body)) return
      } else if (res.status < 500) {
        setState('failed')
        return
      }
    } catch (e) {
      // close()로 끊었으면 끝, 조용해서(stalled) 끊었으면 아래에서 다시 붙는다
      if ((e as Error).name === 'AbortError' && !stalled) return
    } finally {
      window.clearTimeout(idleTimer)
    }
    if (closed) return
    // 끝나지 않았는데 끊겼다: 조금씩 늦추며 다시 붙는다.
    const delay = Math.min(1000 * 2 ** attempt, MAX_BACKOFF_MS)
    setState('reconnecting')
    retryTimer = window.setTimeout(() => connect(attempt + 1), delay)
  }

  /** 스트림을 끝까지 읽는다. DONE·FAILED를 받았거나 닫았으면 true. */
  async function read(body: ReadableStream<Uint8Array>): Promise<boolean> {
    const reader = body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    for (;;) {
      const { value, done } = await reader.read()
      if (done) return closed
      watchIdle()
      buffer += decoder.decode(value, { stream: true })
      const { blocks, rest } = splitBlocks(buffer)
      buffer = rest
      for (const block of blocks) {
        const event = parseBlock(block)
        if (!event) continue
        lastEventId = String(event.seq)
        handlers.onEvent(event)
        if (closed) return true
        if (event.type === 'DONE' || event.type === 'FAILED') {
          setState('done')
          reader.cancel().catch(() => {})
          return true
        }
      }
    }
  }

  void connect(0)
  return { close }
}

/** 작업 하나의 진행 이벤트를 구독한다. taskId가 바뀌면 새로 붙고, 비면 닫는다. */
export function useTaskStream(taskId: MaybeRefOrGetter<string | null | undefined>) {
  const events = ref<TaskEvent[]>([])
  const state = ref<StreamState>('idle')
  let stream: TaskStreamHandle | null = null

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
    stream?.close()
    stream = null
  }

  watch(
    () => toValue(taskId),
    (id) => {
      stop()
      events.value = []
      state.value = 'idle'
      if (id) {
        stream = openTaskStream(id, {
          onEvent: (e) => events.value.push(e),
          onState: (s) => (state.value = s),
        })
      }
    },
    { immediate: true },
  )
  onScopeDispose(stop)

  return { events, state, answer, activeTool, pendingApproval, sources, error, close: stop }
}
