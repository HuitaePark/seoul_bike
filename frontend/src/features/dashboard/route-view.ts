// 지도·타임라인이 같이 쓰는 경로 표시 규칙: 트럭이 지금 구간을 얼마나 지났는지, 승인 대기 변경안을 지도에 어떻게 겹칠지.

import type { Schemas } from '../../shared/api/client'
import { toDemoTruckId, type TruckLive } from '../../shared/dummy'
import type { ApprovalCard } from '../approval/approval'
import { diffMarks } from '../approval/approval.ts'

type Station = Schemas['StationStatus']
type Route = Schemas['Route']

/** 트럭이 가고 있는 구간. next는 다음 정류장 순번(0부터), ratio는 직전 정류장 → next 사이를 지난 비율(0~1). */
export type Leg = { next: number; ratio: number }

/**
 * 지난 정류장(DONE·SKIPPED) 다음 정류장으로 가는 중이면 그 구간을 얼마나 왔는지 구한다.
 * 다음 정류장은 순서상 첫 PENDING이다. DONE 개수로 세면 사이에 건너뛴(SKIPPED) 정류장이 있을 때 그 정류장을 가리킨다.
 * 트럭 GPS 위치를 직전 정류장 → 다음 정류장 직선 위에 투영한다 (권역이 좁아 경도는 위도 cos만 곱해 평면으로 본다).
 * 아직 첫 정류장 전이거나 다 마쳤으면 이을 구간이 없어 null.
 */
export function legOf(route: Route, truck: TruckLive | undefined, byId: Map<string, Station>): Leg | null {
  const next = route.stops.findIndex((s) => (s.status ?? 'PENDING') === 'PENDING')
  if (!truck || next <= 0) return null
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
  const truckId = card.diff?.truckId ?? card.truckId
  const text = [card.summary, ...(card.reasons ?? [])].join(' ')
  const named = stations.filter((s) => text.includes(s.name) || text.includes(s.stationId)).map((s) => s.stationId)
  return {
    approvalId: card.approvalId,
    summary: card.summary,
    // 승인 카드의 원래 ID는 API 결정에 유지하고, 시연 지도와 비교할 ID만 맞춘다.
    truckId: truckId ? toDemoTruckId(truckId) : null,
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
