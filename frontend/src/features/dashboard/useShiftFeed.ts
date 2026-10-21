import { computed, onScopeDispose, reactive, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { openTaskStream, type TaskEvent, type TaskStreamHandle } from '../../shared/composables/useTaskStream'
import type { ApprovalDecision } from '../approval/approval'
import { cardAfter, cardOf, lineAfter, OPEN_STATUSES, pickStreams, sortCards, type Line, type LiveCard } from './feed'

type Task = Schemas['Task']

/** 새 작업(다른 관리자의 요청, 기사 현장 보고)을 찾으려고 목록을 다시 읽는 간격 */
const POLL_MS = 10_000
/** 함께 열어 둘 SSE 수. 나머지 작업은 목록의 pendingApproval로 카드를 맞춘다 (feed.ts pickStreams) */
const MAX_STREAMS = 4

/**
 * 관제 대시보드의 에이전트 소식 (근무조 하나). 승인 대기 카드, 묻기 답, 결정 뒤 결과를 실시간으로 받는다.
 *
 * backend SSE는 작업마다 하나다 (GET /api/ai/tasks/{taskId}/stream). 근무조 단위 스트림은 없어서 이렇게 모은다.
 * - 끝나지 않은 작업 목록을 POLL_MS마다 다시 읽어 새 작업을 찾는다 (GET /api/ai/tasks?shiftId&status).
 * - 그 작업들의 SSE를 열어 승인 요청·답변·도구 진행을 바로 받는다. 처음 열면 backend가 지난 이벤트부터 다시 보내므로
 *   카드는 이벤트만으로 다시 만든다.
 * - 묻기는 QUESTION 작업을 만들고(POST /api/ai/tasks) 그 SSE로 답을 받는다.
 * - 결정은 POST /api/ai/tasks/{taskId}/approval로 보내고, 재개된 에이전트의 결과를 같은 SSE로 받는다.
 */
export function useShiftFeed(shiftId: MaybeRefOrGetter<string>) {
  const cards = reactive(new Map<string, LiveCard>())
  const pending = computed(() => sortCards(cards.values()))
  const log = ref<Line[]>([])
  const qa = ref<{ q: string; a: Line }[]>([])
  const status = ref<'loading' | 'live' | 'offline'>('loading')
  /** 결정을 보내는 중인 승인 요청 */
  const busy = ref<string | null>(null)
  /** 승인 요청별 결정 실패 사유 (409 이미 처리·대체, 403 승인 주체 아님) */
  const errors = reactive<Record<string, string>>({})

  const streams = new Map<string, TaskStreamHandle>()
  /** 내가 묻거나 결정한 작업. 스트림을 먼저 열고, 목록에서 빠져도 끝까지 받는다. */
  const mine = new Set<string>()
  /** 결과를 이어 적을 에이전트 줄. waitFor가 있으면 그 승인 요청을 다시 받은 뒤부터 적는다 (처음부터 다시 보내는 이벤트를 건너뛴다). */
  const replies = new Map<string, { line: Line; waitFor: string | null }>()
  /** DONE·FAILED를 받은 작업. 목록이 늦게 따라와도 다시 열지 않는다. */
  const finished = new Set<string>()
  /** 내가 결정을 보낸 승인 요청. 다시 보내 온 이벤트로 카드가 되살아나지 않게 한다. */
  const decided = new Set<string>()
  let generation = 0
  let lastRefresh = 0

  function say(who: Line['who'], text: string, state: Line['state'] = 'done'): Line {
    const line = reactive<Line>({ who, text, state })
    log.value.push(line)
    return line
  }

  function onEvent(taskId: string, event: TaskEvent, current: boolean) {
    const decidedCard = event.type === 'APPROVAL_REQUIRED' && !!event.approval && decided.has(event.approval.approvalId)
    if (current) {
      const card = decidedCard ? null : cardAfter(cards.get(taskId) ?? null, taskId, event)
      if (card) cards.set(taskId, card)
      else cards.delete(taskId)
    }
    const reply = replies.get(taskId)
    if (reply && !decidedCard && (!reply.waitFor || event.type === 'DONE' || event.type === 'FAILED')) lineAfter(reply.line, event)
    if (reply?.waitFor && event.approval?.approvalId === reply.waitFor) reply.waitFor = null
    if (event.type === 'DONE' || event.type === 'FAILED') {
      finished.add(taskId)
      streams.delete(taskId)
      replies.delete(taskId)
    }
  }

  function watchTask(taskId: string) {
    if (streams.has(taskId) || finished.has(taskId)) return
    const epoch = generation
    streams.set(
      taskId,
      openTaskStream(taskId, {
        onEvent: (e) => onEvent(taskId, e, epoch === generation),
        onState: (s) => {
          if (s !== 'failed') return
          // 403·404 등으로 못 열었다. 다음 목록 읽기에서 다시 열어 본다.
          streams.delete(taskId)
          const reply = replies.get(taskId)
          if (reply) {
            reply.line.state = 'failed'
            reply.line.text = '진행 상황을 받지 못했다. 작업 콘솔에서 확인한다.'
            replies.delete(taskId)
          }
        },
      }),
    )
  }

  /** 목록(끝나지 않은 작업)에 맞춘다: 고른 작업은 SSE를 열고, 나머지는 목록의 카드를 쓴다. */
  function sync(open: Task[]) {
    const wanted = new Set(pickStreams(open.filter((t) => !finished.has(t.taskId)), mine, MAX_STREAMS))
    for (const [taskId, handle] of streams) {
      if (wanted.has(taskId) || mine.has(taskId)) continue
      handle.close()
      streams.delete(taskId)
    }
    for (const task of open) {
      const card = cardOf(task)
      const usable = card && !decided.has(card.approvalId) ? card : null
      if (streams.has(task.taskId)) continue
      if (wanted.has(task.taskId)) {
        // 다시 보내 온 이벤트가 도착하기 전에도 카드가 보이게 목록의 카드로 먼저 채운다
        if (usable) cards.set(task.taskId, usable)
        watchTask(task.taskId)
      } else if (usable) cards.set(task.taskId, usable)
      else cards.delete(task.taskId)
    }
    const listed = new Set(open.map((t) => t.taskId))
    for (const taskId of [...cards.keys()]) {
      if (!listed.has(taskId) && !streams.has(taskId)) cards.delete(taskId)
    }
  }

  async function refresh() {
    const call = ++lastRefresh
    const id = toValue(shiftId)
    try {
      const pages = await Promise.all(
        OPEN_STATUSES.map((s) => api.GET('/api/ai/tasks', { params: { query: { shiftId: id, status: s, size: 50 } } })),
      )
      if (call !== lastRefresh) return
      if (pages.some((p) => p.error || !p.data)) {
        status.value = 'offline'
        return
      }
      status.value = 'live'
      sync(pages.flatMap((p) => p.data!.items))
    } catch {
      if (call === lastRefresh) status.value = 'offline'
    }
  }

  /** 이 화면에 대해 묻는다. 답은 TOKEN이 오는 대로 이어 붙는다. */
  async function ask(text: string) {
    say('me', text)
    const answer = say('agent', '', 'working')
    qa.value.push({ q: text, a: answer })
    try {
      const { data, error } = await api.POST('/api/ai/tasks', { body: { type: 'QUESTION', shiftId: toValue(shiftId), text } })
      if (error || !data) {
        answer.state = 'failed'
        answer.text = problemMessage(error)
        return
      }
      mine.add(data.taskId)
      replies.set(data.taskId, { line: answer, waitFor: null })
      watchTask(data.taskId)
    } catch {
      answer.state = 'failed'
      answer.text = '질문을 보내지 못했다. 연결을 확인한다.'
    }
  }

  /** 승인·거절을 보낸다. 실패하면 errors에 사유를 남기고 다시 보내지 않는다 (응답이 없어도 처리됐을 수 있다). */
  async function decide(card: LiveCard, body: ApprovalDecision): Promise<boolean> {
    busy.value = card.approvalId
    delete errors[card.approvalId]
    try {
      const { error, response } = await api.POST('/api/ai/tasks/{taskId}/approval', {
        params: { path: { taskId: card.taskId } },
        body,
      })
      if (error || !response.ok) {
        errors[card.approvalId] =
          response.status === 409 ? '이미 처리됐거나 더 새로운 변경안으로 대체됐다. 목록을 다시 읽는다.' : problemMessage(error)
        if (response.status === 409) void refresh()
        return false
      }
    } catch {
      errors[card.approvalId] = '결정을 보냈는지 알 수 없다. 작업 콘솔에서 처리 여부를 확인한다.'
      return false
    } finally {
      busy.value = null
    }
    decided.add(card.approvalId)
    cards.delete(card.taskId)
    say('me', `「${card.summary}」 ${body.decision === 'APPROVE' ? '승인' : '거절'}`)
    const line = say('agent', '결정을 에이전트에 넘겼다. 이어지는 결과를 기다린다.', 'working')
    mine.add(card.taskId)
    replies.set(card.taskId, { line, waitFor: streams.has(card.taskId) ? null : card.approvalId })
    watchTask(card.taskId)
    return true
  }

  // 근무조가 바뀌면 그 근무조의 카드로 다시 맞춘다. 내가 묻거나 결정한 작업은 답이 끝날 때까지 받는다.
  watch(
    () => toValue(shiftId),
    () => {
      generation++
      for (const [taskId, handle] of streams) {
        if (replies.has(taskId)) continue
        handle.close()
        streams.delete(taskId)
      }
      cards.clear()
      status.value = 'loading'
      void refresh()
    },
    { immediate: true },
  )
  const timer = window.setInterval(() => void refresh(), POLL_MS)
  onScopeDispose(() => {
    window.clearInterval(timer)
    for (const handle of streams.values()) handle.close()
    streams.clear()
  })

  return { pending, log, qa, status, busy, errors, ask, decide, refresh }
}
