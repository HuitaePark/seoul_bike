import { test } from 'node:test'
import assert from 'node:assert/strict'
import { approvalBlockReason, isTaskId, loadPercent, numberText, reasonText, routeSummary, timeText, validateQuery } from '../src/features/plan-map/model.ts'
import { createDemoSource, demoPlan, demoStations, demoTask, DEMO_REGION, DEMO_SHIFT, DEMO_TASK_ID } from '../src/features/plan-map/demo.ts'

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
  assert.match(reasonText('not_selected'), /단정할 수 없습니다/)
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
  assert.ok(validateQuery({ regionId: DEMO_REGION, shiftId: DEMO_SHIFT, horizonH: 7 }))
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
  await source.decide(DEMO_TASK_ID, { approvalId: oldApproval, decision: 'APPROVE', excludeStationIds: ['ST-101'] })
  const next = (await source.readPlan(DEMO_SHIFT))!
  const nextTask = await source.readTask(DEMO_TASK_ID)
  assert.equal(next.version, 2)
  assert.equal(next.status, 'PROPOSED')
  assert.notEqual(nextTask.pendingApproval!.approvalId, oldApproval)
  assert.equal(nextTask.pendingApproval!.planId, next.planId)
  assert.equal(next.routes.flatMap(r => r.stops).some(s => s.stationId === 'ST-101'), false)
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
  await assert.rejects(source.decide(DEMO_TASK_ID, { approvalId: task.pendingApproval!.approvalId, decision: 'APPROVE', excludeStationIds: ['unknown'] }), /대기 정류장/)
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
