import type { components } from '../../shared/api/schema'

export type Plan = components['schemas']['Plan']
export type Route = components['schemas']['Route']
export type Stop = components['schemas']['Stop']
export type Station = components['schemas']['StationStatus']
export type Task = components['schemas']['Task']
export type Decision = components['schemas']['ApprovalDecision']
export type Role = components['schemas']['User']['role']
export type Query = { regionId: string; shiftId: string; horizonH: number }

export interface PlanReviewSource {
  readPlan(shiftId: string, signal?: AbortSignal): Promise<Plan | null>
  readStations(regionId: string, horizonH: number, signal?: AbortSignal): Promise<Station[]>
  readTask(taskId: string, signal?: AbortSignal): Promise<Task>
  decide(taskId: string, body: Decision, signal?: AbortSignal): Promise<void>
}

export class ReviewError extends Error {
  status?: number
  constructor(message: string, status?: number) {
    super(message)
    this.name = 'ReviewError'
    this.status = status
  }
}

export const planLabels: Record<string, string> = {
  DRAFT: '초안', PROPOSED: '검토 대기', ACTIVE: '운영 중', REJECTED: '거절됨', CLOSED: '종료',
}
export const taskLabels: Record<string, string> = {
  QUEUED: '대기', RUNNING: '처리 중', WAITING_APPROVAL: '승인 대기', DONE: '완료', FAILED: '실패',
}
export const riskLabels: Record<string, string> = { EMPTY: '빈 대여소 위험', OVERFLOW: '과적재 위험', NONE: '위험 없음' }
export const roleLabels: Record<string, string> = { FIXED_PICKUP: '고정 회수', FIXED_DROPOFF: '고정 배치', VARIABLE: '변동' }
export const stopLabels: Record<string, string> = { PENDING: '대기', DONE: '완료', SKIPPED: '건너뜀' }
const reasons: Record<string, string> = {
  BLOCKED: '관리자 제외 또는 현장 차단으로 방문하지 않도록 지정됐습니다.', NO_TRUCKS: '사용 가능한 트럭이 없습니다.',
  CAPACITY_EXCEEDED: '한 번의 작업량이 배정 가능한 트럭 용량을 넘습니다.',
  NOT_SELECTED: '이번 계산에서 선택되지 않았습니다. 시간·적재량 중 한 가지 원인으로 단정할 수 없습니다.',
  NO_SOLUTION: '설정된 탐색 범위에서 경로를 얻지 못했습니다.',
}
export function reasonText(value: string): string {
  return reasons[value] ?? value
}
export function label(labels: Record<string, string>, value?: string): string {
  return value ? labels[value] ?? value : '미제공'
}
export function numberText(value: number | null | undefined): string {
  return value != null && Number.isFinite(value) ? value.toLocaleString('ko-KR', { maximumFractionDigits: 2 }) : '미제공'
}
export function timeText(value?: string | null): string {
  if (!value || !Number.isFinite(Date.parse(value))) return '미제공'
  return new Intl.DateTimeFormat('ko-KR', {
    timeZone: 'Asia/Seoul', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}
export function stationName(id: string, stations: Map<string, Station>, name?: string): string {
  return name?.trim() || stations.get(id)?.name || id
}
export function routeSummary(route: Route) {
  return {
    pickup: route.stops.filter(s => s.action === 'PICKUP').reduce((n, s) => n + s.bikes, 0),
    dropoff: route.stops.filter(s => s.action === 'DROPOFF').reduce((n, s) => n + s.bikes, 0),
    pending: route.stops.filter(s => !s.status || s.status === 'PENDING').length,
    done: route.stops.filter(s => s.status === 'DONE').length,
  }
}
export function loadPercent(load?: number, capacity?: number): number | null {
  if (load == null || capacity == null || !Number.isFinite(load) || !Number.isFinite(capacity) || capacity <= 0) return null
  return Math.min(100, Math.max(0, load / capacity * 100))
}
export function isLoadInvalid(load?: number, capacity?: number): boolean {
  return load != null && (load < 0 || (capacity != null && load > capacity))
}
export function editableStop(stop: Stop): boolean {
  return !stop.status || stop.status === 'PENDING'
}
export function approvalBlockReason(plan: Plan | null, task: Task | null, shiftId: string, manager: boolean): string {
  if (!manager) return '계획 결정은 관리자만 할 수 있습니다.'
  if (!plan) return '먼저 계획을 조회하세요.'
  if (!['DRAFT', 'PROPOSED'].includes(plan.status)) return '검토 대기 중인 계획만 결정할 수 있습니다.'
  if (!task) return '이 계획의 승인 작업을 연결하세요.'
  if (task.shiftId !== shiftId || plan.shiftId !== shiftId) return '근무조가 다른 작업 또는 계획입니다.'
  if (task.status !== 'WAITING_APPROVAL') return '현재 승인 대기 상태가 아닙니다.'
  const approval = task.pendingApproval
  if (!approval || approval.kind !== 'SUBMIT_PLAN' || approval.approver !== 'MANAGER') return '계획 승인 카드가 아닙니다.'
  if (!approval.planId || approval.planId !== plan.planId) return '승인 카드와 조회한 계획이 다릅니다. 최신 안을 다시 조회하세요.'
  return ''
}
export function validateQuery(query: Query): string {
  if (!query.regionId.trim() || !query.shiftId.trim()) return '권역과 근무조를 입력하세요.'
  if (!Number.isInteger(query.horizonH) || query.horizonH < 1 || query.horizonH > 5) return '예측 구간은 1~5시간입니다.'
  return ''
}
export function isTaskId(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}
