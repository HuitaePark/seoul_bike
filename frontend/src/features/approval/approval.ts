// Author: Jung Minkyu
// Date: 20OCT2026
// Purpose: 승인 카드의 규칙(누가 결정할 수 있나, 뺄 수 있는 대여소, 보낼 몸통)을 화면과 떼어 둔다. 관리자·기사 화면이 같이 쓴다.

import type { Schemas } from '../../shared/api/client'

export type Stop = Schemas['Stop']
export type Route = Schemas['Route']
export type ApprovalDecision = Schemas['ApprovalDecision']

/**
 * 승인 카드 (openapi `ApprovalRequest`). `routes`는 에이전트가 계획 승인 카드에 더 싣는 트럭별 경로다
 * (계약 확장 제안 G9, docs/AGENT-GRAPH.md 7절). 계약에 들어가기 전까지 없을 수도 있다.
 */
export type ApprovalCard = Schemas['ApprovalRequest'] & { routes?: Route[] }

export const kindLabel: Record<ApprovalCard['kind'], string> = {
  SUBMIT_PLAN: '계획 활성화',
  ADD_CONSTRAINT: '제약 추가',
  ROUTE_CHANGE: '경로 변경',
}
export const approverLabel: Record<ApprovalCard['approver'], string> = { MANAGER: '관리자 승인', DRIVER: '기사 수락' }
export const gradeLabel: Record<'MINOR' | 'MAJOR', string> = { MINOR: '작은 변경', MAJOR: '큰 변경' }
export const actionLabel: Record<Stop['action'], string> = { PICKUP: '수거', DROPOFF: '보충' }

/**
 * 이 사용자가 카드를 결정할 수 있는가. backend `canBeDecidedBy`와 같은 규칙이다 (어기면 403 NOT_APPROVER).
 * 관리자 카드는 관리자만, 기사 카드는 그 트럭 기사만 결정한다. 권한 판단은 backend가 하고 화면은 버튼만 미리 막는다.
 */
export function canDecide(card: ApprovalCard, user: Pick<Schemas['User'], 'role' | 'truckId'> | null): boolean {
  if (!user) return false
  if (card.approver === 'MANAGER') return user.role === 'MANAGER'
  return user.role === 'DRIVER' && !!card.truckId && user.truckId === card.truckId
}

/**
 * 경로 변경의 승인 단계 (openapi v0.3 `requiredApprovers`). 큰 변경은 관리자 승인 → 기사 수락 두 장이다.
 * 지금 카드의 승인자가 몇 번째인지로 지난 단계·지금 단계·남은 단계를 나눈다. 한 장이면 빈 목록.
 */
export function approvalSteps(card: ApprovalCard): { role: ApprovalCard['approver']; label: string; state: 'done' | 'current' | 'todo' }[] {
  const required = card.requiredApprovers ?? []
  if (required.length < 2) return []
  const current = required.indexOf(card.approver)
  return required.map((role, i) => ({
    role,
    label: approverLabel[role],
    state: i < current ? 'done' : i === current ? 'current' : 'todo',
  }))
}

/** '일부 제외'는 계획 승인 카드에만 있다 (openapi `ApprovalDecision.excludeStationIds`, UR-04). */
export function canExclude(card: ApprovalCard): boolean {
  return card.kind === 'SUBMIT_PLAN'
}

/** 뺄 수 있는 대여소: 카드 경로(G9) → 변경 후 경로 → 문장 속 대여소 ID 순서로 모은다. */
export function stationChoices(card: ApprovalCard): string[] {
  const ids: string[] = []
  const add = (id?: string | null) => {
    if (id && !ids.includes(id)) ids.push(id)
  }
  for (const r of card.routes ?? []) for (const s of r.stops) add(s.stationId)
  for (const s of card.diff?.after ?? []) add(s.stationId)
  for (const text of [card.summary, ...(card.reasons ?? [])]) for (const m of text.matchAll(/ST-\d+/g)) add(m[0])
  return ids
}

/** 경로 차이 표시: 빠지는 대여소는 removed, 새로 들어온 대여소는 added. */
export function diffMarks(card: ApprovalCard): Record<string, 'added' | 'removed'> {
  const before = new Set((card.diff?.before ?? []).map((s) => s.stationId))
  const after = new Set((card.diff?.after ?? []).map((s) => s.stationId))
  const marks: Record<string, 'added' | 'removed'> = {}
  for (const id of before) if (!after.has(id)) marks[id] = 'removed'
  for (const id of after) if (!before.has(id)) marks[id] = 'added'
  return marks
}

/** POST /api/ai/tasks/{taskId}/approval 몸통 (openapi `ApprovalDecision`). */
export function decisionBody(
  card: ApprovalCard,
  decision: ApprovalDecision['decision'],
  opts: { exclude?: string[]; comment?: string } = {},
): ApprovalDecision {
  const body: ApprovalDecision = { approvalId: card.approvalId, decision }
  if (opts.exclude?.length && canExclude(card)) body.excludeStationIds = opts.exclude
  const comment = (opts.comment ?? '').trim()
  if (comment) body.comment = comment.slice(0, 500)
  return body
}
