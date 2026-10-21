import { ReviewError, type Plan, type PlanReviewSource, type Station, type Task } from './model.ts'

export const DEMO_REGION = 'gwangjin-seongdong'
export const DEMO_SHIFT = `${DEMO_REGION}:2026-10-21:AM`
export const DEMO_TASK_ID = 'dd39466c-c263-4da9-8848-38273793215a'
const observedAt = '2026-10-21T08:00:00+09:00'
const names = ['성수역 2번 출구', '서울숲 입구', '뚝섬역 5번 출구', '성수동 주민센터', '건대입구역', '어린이대공원 정문', '화양사거리', '자양동 주민센터']
const ids = ['ST-101', 'ST-102', 'ST-103', 'ST-104', 'ST-201', 'ST-202', 'ST-203', 'ST-301']
export const demoStations: Station[] = ids.map((stationId, index) => ({
  stationId, name: names[index]!, lat: 37.543 + index * 0.001, lon: 127.047 + index * 0.001,
  docks: 20, bikes: [21, 0, 18, 2, 24, 1, 0, 0][index]!, observedAt,
  forecastBikes: [25, -4, 24, 0, 30, -2, -1, null][index],
  risk: [0, 2, 4].includes(index) ? 'OVERFLOW' : 'EMPTY',
  role: [0, 2, 4].includes(index) ? 'FIXED_PICKUP' : index === 7 ? 'VARIABLE' : 'FIXED_DROPOFF',
  elevationM: index === 7 ? null : 12 + index * 2,
}))
export const demoPlan: Plan = {
  planId: 'demo-plan-001-v1', shiftId: DEMO_SHIFT, version: 1, status: 'PROPOSED', provisional: false,
  explanation: '회수 거점과 빈 대여소를 연결한 검토용 시연 계획입니다. 실제 관측·예측·도로 계산 결과가 아닙니다.',
  routes: [
    { truckId: 'truck-1', capacity: 15, startLoad: 4, stops: [
      { seq: 1, stationId: 'ST-101', action: 'PICKUP', bikes: 6, loadAfter: 10, eta: '2026-10-21T08:20:00+09:00', status: 'PENDING', reason: '과적재 위험 거점에서 회수해 서울숲 거점에 보충합니다. (시연)' },
      { seq: 2, stationId: 'ST-102', action: 'DROPOFF', bikes: 7, loadAfter: 3, eta: '2026-10-21T08:40:00+09:00', status: 'PENDING', reason: '예상 재고가 부족한 거점입니다. (시연)' },
      { seq: 3, stationId: 'ST-103', action: 'PICKUP', bikes: 5, loadAfter: 8, eta: '2026-10-21T09:00:00+09:00', status: 'PENDING' },
      { seq: 4, stationId: 'ST-104', action: 'DROPOFF', bikes: 4, loadAfter: 4, eta: '2026-10-21T09:20:00+09:00', status: 'PENDING' },
    ] },
    { truckId: 'truck-2', capacity: 15, startLoad: 0, stops: [
      { seq: 1, stationId: 'ST-201', action: 'PICKUP', bikes: 10, loadAfter: 10, eta: '2026-10-21T08:25:00+09:00', status: 'PENDING' },
      { seq: 2, stationId: 'ST-202', action: 'DROPOFF', bikes: 6, loadAfter: 4, eta: '2026-10-21T08:50:00+09:00', status: 'PENDING' },
      { seq: 3, stationId: 'ST-203', action: 'DROPOFF', bikes: 4, loadAfter: 0, eta: '2026-10-21T09:10:00+09:00', status: 'PENDING' },
    ] },
    { truckId: 'truck-3', capacity: 15, startLoad: 3, stops: [] },
  ],
  unserved: [
    { stationId: 'ST-301', priority: 91.5, reason: 'capacity_exceeded' },
    { stationId: 'ST-302', stationName: '자양사거리 (시연)', priority: 60.2, reason: 'not_selected' },
    { stationId: 'ST-303', stationName: '공사 구간 (시연)', priority: 80, reason: 'blocked' },
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
      if (regionId !== DEMO_REGION || horizonH !== 3) throw new ReviewError('시연 데이터는 기본 권역의 3시간 예측만 제공합니다.')
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
      if (body.decision === 'REJECT') {
        plan.status = 'REJECTED'; task.status = 'DONE'; task.pendingApproval = null
      } else if (body.excludeStationIds?.length) {
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
              plan.unserved.push({ stationId: s.stationId, priority: 50, reason: 'not_selected' })
              return false
            }
            load = next; s.loadAfter = load
            return true
          }).map((s, index) => ({ ...s, seq: index + 1 }))
        }
        plan.version += 1; plan.planId = `demo-plan-001-v${plan.version}`; plan.status = 'PROPOSED'
        plan.explanation = '선택한 거점을 제외한 새 시연 후보입니다. 실제 최적화 결과가 아니며, 다시 검토하고 최종 승인해야 합니다.'
        task.pendingApproval = { ...task.pendingApproval, approvalId: crypto.randomUUID(), planId: plan.planId }
      } else {
        plan.status = 'ACTIVE'; task.status = 'DONE'; task.pendingApproval = null
      }
      task.updatedAt = new Date().toISOString()
    },
  }
}
