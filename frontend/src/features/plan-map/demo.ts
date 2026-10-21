import { ReviewError, type Plan, type PlanReviewSource, type Station, type Task } from './model.ts'

export const DEMO_REGION = 'N02'
export const DEMO_SHIFT = `${DEMO_REGION}:2026-10-21:AM`
export const DEMO_TASK_ID = 'dd39466c-c263-4da9-8848-38273793215a'
const observedAt = '2026-10-21T08:00:00+09:00'
// Reference identity/location from analysis/out/ref_stations.csv (N02).
// Inventory, forecast, route and observations below remain synthetic offline fixtures.
const referenceStations = [
  {"stationId": "ST-102", "name": "자양사거리 광진아크로텔 앞", "lat": 37.53701019, "lon": 127.08224487, "docks": 11, "elevationM": 30.0, "role": "VARIABLE"},
  {"stationId": "ST-2308", "name": "건대병원후문", "lat": 37.53956604, "lon": 127.0724411, "docks": 12, "elevationM": 17.0, "role": "VARIABLE"},
  {"stationId": "ST-97", "name": "어린이대공원역 3번출구 앞", "lat": 37.54697037, "lon": 127.07466888, "docks": 5, "elevationM": 30.0, "role": "VARIABLE"},
  {"stationId": "ST-253", "name": "군자역 7번출구 베스트샵 앞", "lat": 37.55603027, "lon": 127.0786438, "docks": 8, "elevationM": 25.0, "role": "VARIABLE"},
  {"stationId": "ST-3121", "name": "성수역 1번출구 주변", "lat": 37.54555893, "lon": 127.05336761, "docks": 8, "elevationM": 23.0, "role": "VARIABLE"},
  {"stationId": "ST-2309", "name": "구의역 리버비스타 오피스텔", "lat": 37.53780746, "lon": 127.08976746, "docks": 15, "elevationM": 27.0, "role": "VARIABLE"},
  {"stationId": "ST-3390", "name": "한강버스 뚝섬 선착장", "lat": 37.52961349, "lon": 127.06626129, "docks": 30, "elevationM": 8.0, "role": "VARIABLE"},
  {"stationId": "ST-988", "name": "신양초교앞 교차로", "lat": 37.5352211, "lon": 127.06839752, "docks": 12, "elevationM": 24.0, "role": "VARIABLE"}
] as const
export const demoStations: Station[] = referenceStations.map((station, index) => ({
  ...station, bikes: [21, 0, 18, 2, 24, 1, 0, 0][index]!, observedAt,
  forecastBikes: [25, 0, 24, 0, 30, 0, 0, null][index],
  risk: [0, 2, 4].includes(index) ? 'OVERFLOW' : 'EMPTY',
}))
export const demoPlan: Plan = {
  planId: 'demo-plan-001-v1', shiftId: DEMO_SHIFT, version: 1, status: 'PROPOSED', provisional: false,
  explanation: '회수 거점과 빈 대여소를 연결한 검토용 시연 계획입니다. 실제 관측·예측·도로 계산 결과가 아닙니다.',
  routes: [
    { truckId: 'TR-03', capacity: 15, startLoad: 4, stops: [
      { seq: 1, stationId: 'ST-102', action: 'PICKUP', bikes: 6, loadAfter: 10, eta: '2026-10-21T08:20:00+09:00', status: 'PENDING', reason: '과적재 위험 거점에서 회수해 빈 대여소에 보충합니다. (시연)' },
      { seq: 2, stationId: 'ST-2308', action: 'DROPOFF', bikes: 7, loadAfter: 3, eta: '2026-10-21T08:40:00+09:00', status: 'PENDING', reason: '예상 재고가 부족한 거점입니다. (시연)' },
      { seq: 3, stationId: 'ST-97', action: 'PICKUP', bikes: 5, loadAfter: 8, eta: '2026-10-21T09:00:00+09:00', status: 'PENDING' },
      { seq: 4, stationId: 'ST-253', action: 'DROPOFF', bikes: 4, loadAfter: 4, eta: '2026-10-21T09:20:00+09:00', status: 'PENDING' },
    ] },
    { truckId: 'TR-04', capacity: 15, startLoad: 0, stops: [
      { seq: 1, stationId: 'ST-3121', action: 'PICKUP', bikes: 10, loadAfter: 10, eta: '2026-10-21T08:25:00+09:00', status: 'PENDING' },
      { seq: 2, stationId: 'ST-2309', action: 'DROPOFF', bikes: 6, loadAfter: 4, eta: '2026-10-21T08:50:00+09:00', status: 'PENDING' },
      { seq: 3, stationId: 'ST-3390', action: 'DROPOFF', bikes: 4, loadAfter: 0, eta: '2026-10-21T09:10:00+09:00', status: 'PENDING' },
    ] },
    { truckId: 'TR-05', capacity: 15, startLoad: 3, stops: [] },
  ],
  unserved: [
    { stationId: 'ST-988', priority: 91.5, reason: 'CAPACITY_EXCEEDED' },
    { stationId: 'ST-98', stationName: '광진문화예술회관 앞', priority: 60.2, reason: 'NOT_SELECTED' },
    { stationId: 'ST-114', stationName: '광진메디칼 앞', priority: 80, reason: 'BLOCKED' },
  ],
}
export function demoTask(plan: Plan = demoPlan): Task {
  return {
    taskId: DEMO_TASK_ID, shiftId: plan.shiftId, type: 'PLAN_DRAFT', status: 'WAITING_APPROVAL',
    createdBy: 'demo-manager', createdAt: observedAt, updatedAt: observedAt, traceId: 'demo-trace',
    pendingApproval: { approvalId: '83e33026-1a98-4cc2-ae4b-32ad9d9ddf49', kind: 'SUBMIT_PLAN', approver: 'MANAGER',
      planId: plan.planId, summary: '근무조 계획을 검토해 주세요.', reasons: ['트럭별 경로와 미처리 거점을 확인하세요.'] },
  }
}
async function pause(signal?: AbortSignal) {
  await new Promise<void>((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('취소됨', 'AbortError')) }
    const timer = setTimeout(() => { signal?.removeEventListener('abort', abort); resolve() }, 120)
    if (signal?.aborted) abort()
    else signal?.addEventListener('abort', abort, { once: true })
  })
}

/** Offline fixture responses only. This is not the optimizer or a production approval service. */
export function createDemoSource(): PlanReviewSource {
  let plan = structuredClone(demoPlan)
  const task = demoTask(plan)
  return {
    async readPlan(shiftId, signal) { await pause(signal); return shiftId === plan.shiftId ? structuredClone(plan) : null },
    async readStations(regionId, horizonH, signal) {
      await pause(signal)
      if (regionId !== DEMO_REGION || horizonH !== 3) throw new ReviewError('시연 데이터는 N02 구역의 3시간 예측만 제공합니다.')
      return structuredClone(demoStations)
    },
    async readTask(taskId, signal) {
      await pause(signal)
      if (taskId !== task.taskId) throw new ReviewError('시연 작업을 찾을 수 없습니다.', 404)
      return structuredClone(task)
    },
    async decide(taskId, body, signal) {
      await pause(signal)
      if (taskId !== task.taskId || task.status !== 'WAITING_APPROVAL' || body.approvalId !== task.pendingApproval?.approvalId) {
        throw new ReviewError('이미 처리됐거나 다른 승인 요청입니다.', 409)
      }
      if (body.excludeStationIds?.length) {
        const excluded = new Set(body.excludeStationIds)
        const eligible = new Set(plan.routes.flatMap(r => r.stops.filter(s => !s.status || s.status === 'PENDING').map(s => s.stationId)))
        if ([...excluded].some(id => !eligible.has(id))) throw new ReviewError('계획의 대기 정류장만 제외할 수 있습니다.', 400)
        // A small fixture transformation, not an OR-Tools result: keep feasible
        // remaining stops in their old order and expose skipped work as unserved.
        plan = structuredClone(plan)
        for (const route of plan.routes) {
          let load = route.startLoad ?? 0
          route.stops = route.stops.filter(s => !excluded.has(s.stationId)).filter(s => {
            const next = load + (s.action === 'PICKUP' ? s.bikes : -s.bikes)
            if (next < 0 || next > (route.capacity ?? 0)) {
              plan.unserved.push({ stationId: s.stationId, priority: 50, reason: 'NOT_SELECTED' })
              return false
            }
            load = next; s.loadAfter = load
            return true
          }).map((s, index) => ({ ...s, seq: index + 1 }))
        }
        plan.version += 1; plan.planId = `demo-plan-001-v${plan.version}`; plan.status = 'PROPOSED'
        plan.explanation = '선택한 거점을 제외한 새 시연 후보입니다. 실제 최적화 결과가 아니며, 다시 검토하고 최종 승인해야 합니다.'
        task.pendingApproval = { ...task.pendingApproval, approvalId: crypto.randomUUID(), planId: plan.planId }
      } else if (body.decision === 'REJECT') {
        plan.status = 'REJECTED'; task.status = 'DONE'; task.pendingApproval = null
      } else {
        plan.status = 'ACTIVE'; task.status = 'DONE'; task.pendingApproval = null
      }
      task.updatedAt = new Date().toISOString()
    },
  }
}
