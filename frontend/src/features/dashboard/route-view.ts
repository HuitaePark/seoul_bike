// 지도·타임라인이 같이 쓰는 경로 표시 규칙: 트럭이 지금 구간을 얼마나 지났는지, 승인 대기 변경안을 지도에 어떻게 겹칠지.

import type { Schemas } from '../../shared/api/client'
import type { ApprovalCard } from '../approval/approval'
import { diffMarks } from '../approval/approval.ts'

type Station = Schemas['StationStatus']
type Route = Schemas['Route']
type Plan = Schemas['Plan']

/** 경로에 capacity가 없을 때 쓰는 트럭 용량 (계약 Route.capacity 예시값) */
const DEFAULT_CAPACITY = 15

/**
 * 트럭 한 대의 지금 적재와 위치. 트럭 위치 API가 계약에 아직 없어(GPS 없음) 계획 진행으로 구한다.
 * backend TruckStates(에이전트 `get_truck_status`)와 같은 규칙이다: 위치는 마지막으로 마친(DONE) 정류장,
 * 적재는 그 정류장의 loadAfter, 마친 곳이 없으면 경로의 startLoad. 마친 곳이 없으면 차고지라 지도에는 놓지 않는다(위치 null).
 */
export type TruckLive = {
  truckId: string
  load: number
  capacity: number
  /** 위치 근거가 된 정류장 (마지막으로 마친 곳). 아직 출발 전이면 null */
  stationId: string | null
  lat: number | null
  lon: number | null
}

export function truckOfRoute(route: Route, byId: Map<string, Station>): TruckLive {
  const last = route.stops.filter((s) => s.status === 'DONE').at(-1)
  const at = last ? byId.get(last.stationId) : undefined
  return {
    truckId: route.truckId,
    load: last?.loadAfter ?? route.startLoad ?? 0,
    capacity: route.capacity ?? DEFAULT_CAPACITY,
    stationId: last?.stationId ?? null,
    lat: at?.lat ?? null,
    lon: at?.lon ?? null,
  }
}

/** 지도에 놓을 수 있는(위치를 아는) 트럭 */
export const placed = (t: TruckLive): t is TruckLive & { lat: number; lon: number } => t.lat != null && t.lon != null

/**
 * 계획 승인 카드의 경로를 같은 계획의 전체 경로로 바꾼다 (#91). business-mcp는 응답 길이를 맞추려고
 * 정류장 eta·이름을 먼저 빼고 뒤쪽 정류장을 자른다. 화면은 `GET /api/shifts/{shiftId}/plan`으로 같은 계획(planId)을
 * 받았으면 그 경로를 보여 준다. 다른 계획이거나 계획을 못 받았으면 카드 그대로다.
 */
export function withPlanRoutes<C extends ApprovalCard>(card: C, plan: Plan | null): C {
  if (card.kind !== 'SUBMIT_PLAN' || !plan || !card.planId || card.planId !== plan.planId) return card
  return { ...card, routes: plan.routes.filter((r) => r.stops.length) }
}

/** 트럭이 가고 있는 구간. next는 다음 정류장 순번(0부터), ratio는 직전 정류장 → next 사이를 지난 비율(0~1). */
export type Leg = { next: number; ratio: number }

/**
 * 지난 정류장(DONE·SKIPPED) 다음 정류장으로 가는 중이면 그 구간을 얼마나 왔는지 구한다.
 * 다음 정류장은 순서상 첫 PENDING이다. DONE 개수로 세면 사이에 건너뛴(SKIPPED) 정류장이 있을 때 그 정류장을 가리킨다.
 * 트럭 위치를 직전 정류장 → 다음 정류장 직선 위에 투영한다 (권역이 좁아 경도는 위도 cos만 곱해 평면으로 본다).
 * 지금 위치는 마지막으로 마친 정류장이라(GPS 없음, TruckLive) ratio는 0이다. 위치 API가 생기면 그대로 중간 지점이 된다.
 * 아직 첫 정류장 전이거나 다 마쳤거나 위치를 모르면 이을 구간이 없어 null.
 */
export function legOf(route: Route, truck: TruckLive | undefined, byId: Map<string, Station>): Leg | null {
  const next = route.stops.findIndex((s) => (s.status ?? 'PENDING') === 'PENDING')
  if (!truck || !placed(truck) || next <= 0) return null
  const a = byId.get(route.stops[next - 1]!.stationId)
  const b = byId.get(route.stops[next]!.stationId)
  if (!a || !b) return null
  const k = Math.cos((a.lat * Math.PI) / 180)
  const [bx, by] = [(b.lon - a.lon) * k, b.lat - a.lat]
  const [px, py] = [(truck.lon - a.lon) * k, truck.lat - a.lat]
  const len2 = bx * bx + by * by
  const ratio = len2 ? (px * bx + py * by) / len2 : 0
  return { next, ratio: Math.min(1, Math.max(0, ratio)) }
}

/** 두 점 사이 ratio 지점 (화면 좌표) */
export function lerp(p: [number, number], q: [number, number], ratio: number): [number, number] {
  return [p[0] + (q[0] - p[0]) * ratio, p[1] + (q[1] - p[1]) * ratio]
}

/**
 * 경로 선을 트럭 위치에서 끊는다. 지난 구간은 첫 정류장 → 트럭, 남은 구간은 트럭 → 마지막 정류장이다.
 * 구간을 모르면(첫 정류장 전·다 마침) 마지막으로 마친 정류장에서 끊는다.
 */
export function splitAtTruck(pts: [number, number][], done: number, leg: Leg | null) {
  if (leg && pts[leg.next - 1] && pts[leg.next]) {
    const tip = lerp(pts[leg.next - 1]!, pts[leg.next]!, leg.ratio)
    return { done: [...pts.slice(0, leg.next), tip], todo: [tip, ...pts.slice(leg.next)], tip }
  }
  return { done: pts.slice(0, Math.max(done, 1)), todo: pts.slice(Math.max(done - 1, 0)), tip: null }
}

/** 승인 대기 변경안을 지도에 겹쳐 보는 내용 */
export type RoutePreview = {
  approvalId: string
  summary: string
  /** 바뀌는 트럭. 제약 추가처럼 트럭이 없으면 null */
  truckId: string | null
  /** 바뀐 뒤 남은 정류장 순서 */
  after: string[]
  added: string[]
  removed: string[]
  /** 지도에서 밝게 둘 대여소 (변경 전·후 정류장, 문장에 나온 대여소) */
  stations: string[]
}

/**
 * 승인 카드 → 지도 미리보기. 경로 변경은 diff의 변경 전·후를, 경로가 없는 카드(제약 추가)는
 * 요약·사유 문장에 나온 대여소 이름이나 ID를 강조한다.
 */
export function previewOf(card: ApprovalCard, stations: Station[]): RoutePreview {
  const marks = diffMarks(card)
  const before = (card.diff?.before ?? []).map((s) => s.stationId)
  const after = (card.diff?.after ?? []).map((s) => s.stationId)
  const text = [card.summary, ...(card.reasons ?? [])].join(' ')
  const named = stations.filter((s) => text.includes(s.name) || text.includes(s.stationId)).map((s) => s.stationId)
  return {
    approvalId: card.approvalId,
    summary: card.summary,
    truckId: card.diff?.truckId ?? card.truckId ?? null,
    after,
    added: Object.keys(marks).filter((id) => marks[id] === 'added'),
    removed: Object.keys(marks).filter((id) => marks[id] === 'removed'),
    stations: [...new Set([...before, ...after, ...named])],
  }
}

export type PreviewMark = { id: string; kind: 'added' | 'removed' | 'named' }
export const markText: Record<PreviewMark['kind'], string> = { added: '+ 추가', removed: '✕ 빠짐', named: '! 변경' }

/** 지도에 붙일 변경안 표시. 경로가 없는 변경(제약 추가)은 문장에 나온 대여소를 짚는다. */
export function previewMarks(p: RoutePreview | null | undefined): PreviewMark[] {
  if (!p) return []
  return [
    ...p.removed.map((id) => ({ id, kind: 'removed' as const })),
    ...p.added.map((id) => ({ id, kind: 'added' as const })),
    ...(p.truckId ? [] : p.stations.map((id) => ({ id, kind: 'named' as const }))),
  ]
}
