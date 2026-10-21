import { test } from 'node:test'
import assert from 'node:assert/strict'
import { approvalBlockReason, isTaskId, loadPercent, numberText, reasonText, routeSummary, timeText, validateQuery } from '../src/features/plan-map/model.ts'
import { createDemoSource, demoPlan, demoStations, demoTask, DEMO_REGION, DEMO_SHIFT, DEMO_TASK_ID } from '../src/features/plan-map/demo.ts'
import { mapData, position, truckColor } from '../src/features/plan-map/map-model.ts'
import type { Station } from '../src/features/plan-map/model.ts'
import { decisionBody } from '../src/features/approval/approval.ts'

test('0을 보존하고 선택 필드의 누락을 기본값으로 채우지 않는다', () => {
  assert.equal(numberText(0), '0')
  assert.equal(numberText(null), '미제공')
  assert.equal(numberText(undefined), '미제공')
  assert.equal(loadPercent(undefined, 15), null)
  assert.equal(loadPercent(5, undefined), null)
  assert.equal(loadPercent(0, 15), 0)
  assert.equal(loadPercent(30, 15), 100)
  assert.equal(loadPercent(1, 0), null)
})
test('시각은 브라우저 시간대와 관계없이 한국 시간으로 표시한다', () => {
  assert.match(timeText('2026-10-20T23:20:00Z'), /08:20/)
  assert.equal(timeText('invalid'), '미제공')
  assert.equal(timeText(null), '미제공')
})
test('미처리 원인은 제공된 코드의 의미만 설명한다', () => {
  assert.match(reasonText('NOT_SELECTED'), /단정할 수 없습니다/)
  assert.equal(reasonText('future_reason'), 'future_reason')
})
test('동일 근무조·계획의 관리자 승인 카드만 결정할 수 있다', () => {
  assert.equal(approvalBlockReason(demoPlan, demoTask(), DEMO_SHIFT, true), '')
  assert.ok(approvalBlockReason(demoPlan, demoTask(), DEMO_SHIFT, false))
  assert.ok(approvalBlockReason(demoPlan, { ...demoTask(), shiftId: 'another' }, DEMO_SHIFT, true))
  assert.ok(approvalBlockReason(demoPlan, { ...demoTask(), status: 'DONE' }, DEMO_SHIFT, true))
  for (const change of [{ planId: 'old-plan' }, { kind: 'ROUTE_CHANGE' as const }, { approver: 'DRIVER' as const }]) {
    const task = demoTask()
    Object.assign(task.pendingApproval!, change)
    assert.ok(approvalBlockReason(demoPlan, task, DEMO_SHIFT, true))
  }
  assert.ok(approvalBlockReason({ ...demoPlan, status: 'ACTIVE' }, demoTask(), DEMO_SHIFT, true))
})
test('조회 조건과 작업 UUID를 검증한다', () => {
  assert.equal(validateQuery({ regionId: DEMO_REGION, shiftId: DEMO_SHIFT, horizonH: 3 }), '')
  assert.ok(validateQuery({ regionId: '', shiftId: DEMO_SHIFT, horizonH: 3 }))
  assert.equal(validateQuery({ regionId: DEMO_REGION, shiftId: DEMO_SHIFT, horizonH: 1 }), '')
  assert.equal(validateQuery({ regionId: DEMO_REGION, shiftId: DEMO_SHIFT, horizonH: 5 }), '')
  for (const horizonH of [0, 6, 7, 1.5, Number.NaN]) {
    assert.ok(validateQuery({ regionId: DEMO_REGION, shiftId: DEMO_SHIFT, horizonH }))
  }
  assert.equal(isTaskId(DEMO_TASK_ID), true)
  assert.equal(isTaskId('../tasks'), false)
})
test('빈 경로와 완료·대기 작업량을 구분한다', () => {
  assert.deepEqual(routeSummary({ truckId: 'empty', stops: [] }), { pickup: 0, dropoff: 0, pending: 0, done: 0 })
  assert.deepEqual(routeSummary(demoPlan.routes[0]!), { pickup: 11, dropoff: 11, pending: 4, done: 0 })
})
test('시연 제외는 새 승인 카드가 생기며 새 후보를 최종 승인해야 ACTIVE가 된다', async () => {
  const source = createDemoSource()
  const task = await source.readTask(DEMO_TASK_ID)
  const oldApproval = task.pendingApproval!.approvalId
  await source.decide(DEMO_TASK_ID, decisionBody(task.pendingApproval!, 'REJECT', { exclude: ['ST-102'] }))
  const next = (await source.readPlan(DEMO_SHIFT))!
  const nextTask = await source.readTask(DEMO_TASK_ID)
  assert.equal(next.version, 2)
  assert.equal(next.status, 'PROPOSED')
  assert.notEqual(nextTask.pendingApproval!.approvalId, oldApproval)
  assert.equal(nextTask.pendingApproval!.planId, next.planId)
  assert.equal(next.routes.flatMap(r => r.stops).some(s => s.stationId === 'ST-102'), false)
  assert.equal(approvalBlockReason(next, nextTask, DEMO_SHIFT, true), '')
  await assert.rejects(source.decide(DEMO_TASK_ID, { approvalId: oldApproval, decision: 'APPROVE' }), /이미 처리/)
  await source.decide(DEMO_TASK_ID, { approvalId: nextTask.pendingApproval!.approvalId, decision: 'APPROVE' })
  assert.equal((await source.readPlan(DEMO_SHIFT))!.status, 'ACTIVE')
  assert.equal((await source.readTask(DEMO_TASK_ID)).pendingApproval, null)
})
test('시연 원본·응답을 변경해도 다른 세션에 영향을 주지 않는다', async () => {
  const source = createDemoSource()
  const plan = (await source.readPlan(DEMO_SHIFT))!
  plan.routes.length = 0
  assert.equal((await source.readPlan(DEMO_SHIFT))!.routes.length, 3)
  const task = await source.readTask(DEMO_TASK_ID)
  await assert.rejects(source.decide(DEMO_TASK_ID, decisionBody(task.pendingApproval!, 'REJECT', { exclude: ['unknown'] })), /대기 정류장/)
  await source.decide(DEMO_TASK_ID, { approvalId: task.pendingApproval!.approvalId, decision: 'REJECT' })
  assert.equal((await source.readPlan(DEMO_SHIFT))!.status, 'REJECTED')
  assert.equal(demoPlan.status, 'PROPOSED')
  assert.equal(demoStations.length, 8)
  assert.equal((await createDemoSource().readPlan(DEMO_SHIFT))!.status, 'PROPOSED')
})
test('시연 조회는 취소할 수 있고 없는 근무조는 계획 없음으로 반환한다', async () => {
  const source = createDemoSource()
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(source.readPlan(DEMO_SHIFT, controller.signal), { name: 'AbortError' })
  assert.equal(await source.readPlan('missing'), null)
})

test('지도 좌표는 경도·위도 순서이며 0을 보존하고 잘못된 좌표를 거른다', () => {
  assert.deepEqual(position({ lat: 37.5, lon: 127 }), [127, 37.5])
  assert.deepEqual(position({ lat: 0, lon: 0 }), [0, 0])
  assert.equal(position({ lat: NaN, lon: 127 }), null)
  assert.equal(position({ lat: 90, lon: 127 }), null)
  assert.equal(position({ lat: 37.5, lon: 181 }), null)
  assert.equal(position({ lat: null, lon: null } as unknown as Station), null)
  assert.equal(position(), null)
})
test('원본을 변경하지 않고 seq 순서대로 방문 연결선을 만든다', () => {
  const plan = structuredClone(demoPlan)
  plan.routes[0]!.stops.reverse()
<<<<<<< HEAD
  const result = mapData(demoStations, plan, 'TR-01', [])
  const leg = result.lines.features.find(f => f.properties.truckId === 'TR-01')!
=======
  const result = mapData(demoStations, plan, 'TR-03', [])
  const leg = result.lines.features.find(f => f.properties.truckId === 'TR-03')!
>>>>>>> 2b4b4e8491d564abe346b6cc9934c8eb91e82ede
  assert.equal(leg.properties.fromSeq, 1)
  assert.equal(leg.properties.toSeq, 2)
  assert.deepEqual(leg.geometry.coordinates, [position(demoStations[0]), position(demoStations[1])])
  assert.equal(plan.routes[0]!.stops[0]!.seq, 4)
  assert.equal(result.lines.features.filter(f => f.properties.selected).length, 3)
  assert.equal(result.lines.features.filter(f => !f.properties.selected).length, 2)
})
test('좌표가 없는 중간 거점을 건너 연결하거나 미처리 좌표를 만들어내지 않는다', () => {
<<<<<<< HEAD
  const stations = demoStations.filter(s => s.stationId !== 'ST-102')
  const result = mapData(stations, demoPlan, 'TR-01', [])
=======
  const stations = demoStations.filter(s => s.stationId !== 'ST-1660')
  const result = mapData(stations, demoPlan, 'TR-03', [])
>>>>>>> 2b4b4e8491d564abe346b6cc9934c8eb91e82ede
  assert.deepEqual(result.lines.features.filter(f => f.properties.selected).map(f => [f.properties.fromSeq, f.properties.toSeq]), [[3, 4]])
  assert.deepEqual(result.missingIds.sort(), ['ST-114', 'ST-1660', 'ST-98'])
  assert.equal(result.points.some(p => p.station.stationId === 'ST-98'), false)
})
test('제외 선택은 현재 계획 경로를 바꾸지 않고 마커에 표시한다', () => {
<<<<<<< HEAD
  const result = mapData(demoStations, demoPlan, 'TR-01', ['ST-101'])
  assert.equal(result.points.find(p => p.station.stationId === 'ST-101')!.excluded, true)
  assert.deepEqual(result.points.find(p => p.station.stationId === 'ST-101')!.visits, [{ seq: 1, action: 'PICKUP', bikes: 6 }])
  assert.equal(result.points.find(p => p.station.stationId === 'ST-301')!.unserved, true)
  assert.equal(result.lines.features.filter(f => f.properties.selected).length, 3)
  assert.equal(mapData(demoStations, demoPlan, 'TR-03', []).selectedPositions.length, 0)
=======
  const result = mapData(demoStations, demoPlan, 'TR-03', ['ST-102'])
  assert.equal(result.points.find(p => p.station.stationId === 'ST-102')!.excluded, true)
  assert.deepEqual(result.points.find(p => p.station.stationId === 'ST-102')!.visits, [{ seq: 1, action: 'PICKUP', bikes: 6 }])
  assert.equal(result.points.find(p => p.station.stationId === 'ST-988')!.unserved, true)
  assert.equal(result.lines.features.filter(f => f.properties.selected).length, 3)
  assert.equal(mapData(demoStations, demoPlan, 'TR-05', []).selectedPositions.length, 0)
>>>>>>> 2b4b4e8491d564abe346b6cc9934c8eb91e82ede
})
test('계획이 없어도 대여소를 표시하고 잘못된 현황 좌표를 별도로 센다', () => {
  const stations = structuredClone(demoStations)
  stations[0]!.lat = Infinity
  const result = mapData(stations, null, '', [])
  assert.equal(result.points.length, 7)
  assert.equal(result.invalidCount, 1)
  assert.deepEqual(result.lines.features, [])
  assert.deepEqual(result.missingIds, [])
})
test('트럭 배열 순서가 바뀌어도 색상을 유지하고 한 거점의 여러 방문 번호를 보존한다', () => {
  const plan = structuredClone(demoPlan)
  plan.routes.reverse()
<<<<<<< HEAD
  assert.equal(truckColor(plan, 'TR-01'), truckColor(demoPlan, 'TR-01'))
  plan.routes.find(r => r.truckId === 'TR-01')!.stops.push({ seq: 5, stationId: 'ST-101', action: 'PICKUP', bikes: 1 })
  assert.deepEqual(mapData(demoStations, plan, 'TR-01', []).points.find(p => p.station.stationId === 'ST-101')!.visits.map(s => s.seq), [1, 5])
=======
  assert.equal(truckColor(plan, 'TR-03'), truckColor(demoPlan, 'TR-03'))
  plan.routes.find(r => r.truckId === 'TR-03')!.stops.push({ seq: 5, stationId: 'ST-102', action: 'PICKUP', bikes: 1 })
  assert.deepEqual(mapData(demoStations, plan, 'TR-03', []).points.find(p => p.station.stationId === 'ST-102')!.visits.map(s => s.seq), [1, 5])
>>>>>>> 2b4b4e8491d564abe346b6cc9934c8eb91e82ede
})
