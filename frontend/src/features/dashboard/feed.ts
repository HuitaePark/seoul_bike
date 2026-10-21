// 관제 대시보드의 에이전트 소식 규칙: 근무조 작업의 진행 이벤트(SSE)를 승인 대기 카드와 대화 줄로 바꾼다.
// 가져오기·구독은 useShiftFeed가 하고, 여기는 fetch·Vue 없이 node --test로 시험한다.

import type { Schemas } from '../../shared/api/client'
import { approverLabel, type ApprovalCard } from '../approval/approval.ts'

type Task = Schemas['Task']
type TaskEvent = Schemas['TaskEvent']

/** 승인 대기 카드와 그 카드가 걸린 작업. since는 카드가 올라온 시각이다. */
export type LiveCard = ApprovalCard & { taskId: string; since: string }

/** 대화 한 줄. 에이전트 줄은 진행 이벤트가 올 때마다 바뀐다 (working → done·failed). */
export type Line = {
  who: 'me' | 'agent'
  text: string
  state: 'working' | 'done' | 'failed'
  /** 지금 실행 중인 도구 이름 (예: 위험 대여소 조회 중) */
  tool?: string
  /** 지금까지 받은 TOKEN. DONE의 answer가 오면 그것으로 바꾼다. */
  streamed?: string
}

/** 아직 끝나지 않아 이벤트가 더 올 작업 */
export const OPEN_STATUSES = ['QUEUED', 'RUNNING', 'WAITING_APPROVAL'] as const

/** 목록 API의 작업 → 승인 대기 카드. 승인을 기다리는 작업만 카드가 있다. */
export function cardOf(task: Task): LiveCard | null {
  return task.status === 'WAITING_APPROVAL' && task.pendingApproval
    ? { ...task.pendingApproval, taskId: task.taskId, since: task.updatedAt }
    : null
}

/**
 * 진행 이벤트 하나를 그 작업의 카드에 반영한다. 승인 요청이면 새 카드다 (앞 카드는 대체된다).
 * 그 뒤에 다른 이벤트가 오면 결정이 받아들여져 에이전트가 다시 움직인 것이라 카드를 내린다.
 */
export function cardAfter(card: LiveCard | null, taskId: string, event: TaskEvent): LiveCard | null {
  if (event.type !== 'APPROVAL_REQUIRED') return null
  return event.approval ? { ...event.approval, taskId, since: event.occurredAt } : card
}

/**
 * 에이전트 줄에 진행 이벤트를 반영한다. 도구는 상태로 보이고 TOKEN은 이어 붙인다.
 * 다음 승인을 기다리거나 끝나면 줄을 닫는다. 승인 뒤 에이전트가 다시 움직이면 같은 줄이 다시 진행 중이 된다.
 */
export function lineAfter(line: Line, event: TaskEvent): void {
  switch (event.type) {
    case 'TOOL_START':
      line.state = 'working'
      line.tool = event.tool?.label ?? event.tool?.name
      break
    case 'TOOL_END':
      line.tool = undefined
      break
    case 'TOKEN':
      line.state = 'working'
      line.streamed = (line.streamed ?? '') + (event.token ?? '')
      line.text = line.streamed
      break
    case 'APPROVAL_REQUIRED':
      // 에이전트가 방금 말한 설명은 남기고, 누구의 승인을 기다리는지 뒤에 붙인다
      if (event.approval) {
        const waiting = `${approverLabel[event.approval.approver]}을 기다린다: ${event.approval.summary}`
        line.text = line.streamed ? `${line.streamed}\n→ ${waiting}` : waiting
      }
      line.tool = undefined
      line.streamed = undefined
      line.state = 'done'
      break
    case 'DONE':
      line.tool = undefined
      line.state = 'done'
      line.text = event.answer || line.streamed || '작업을 마쳤다.'
      line.streamed = undefined
      break
    case 'FAILED':
      line.tool = undefined
      line.streamed = undefined
      line.state = 'failed'
      line.text = event.error || '작업이 실패했다.'
      break
  }
}

/**
 * 열어 둘 스트림. 내가 묻거나 결정한 작업 먼저, 그다음 최근 작업 순으로 max개까지.
 * 브라우저는 한 출처에 HTTP/1.1 연결을 6개까지만 열어서, 스트림이 다 차지하면 다른 API 호출이 막힌다.
 * 고르지 못한 작업은 목록을 다시 읽어(폴링) 카드를 맞춘다.
 */
export function pickStreams(open: Task[], mine: ReadonlySet<string>, max: number): string[] {
  return [...open]
    .sort((a, b) => Number(mine.has(b.taskId)) - Number(mine.has(a.taskId)) || Date.parse(b.createdAt) - Date.parse(a.createdAt))
    .slice(0, max)
    .map((t) => t.taskId)
}

/** 카드를 최근에 올라온 순서로 */
export function sortCards(cards: Iterable<LiveCard>): LiveCard[] {
  return [...cards].sort((a, b) => Date.parse(b.since) - Date.parse(a.since))
}

/** 몇 분 전 (0 이상) */
export function minutesAgo(iso: string, now: number): number {
  return Math.max(0, Math.round((now - Date.parse(iso)) / 60_000))
}
