import { test, expect, type Page } from '@playwright/test'
import { demoPlan, demoStations, demoTask, DEMO_REGION, DEMO_SHIFT, DEMO_TASK_ID } from '../../src/features/plan-map/demo'
import type { Plan, Station, Task } from '../../src/features/plan-map/model'

// Use the real map renderer/worker with a local, deterministic basemap style.
// External provider availability is checked separately during visual verification.
test.beforeEach(async ({ page }) => {
  await page.context().route('https://tiles.openfreemap.org/**', route => route.fulfill({ json: {
    version: 8, sources: {}, layers: [{ id: 'test-background', type: 'background', paint: { 'background-color': '#e8eef4' } }],
  } }))
})

async function login(page: Page, role = 'MANAGER') {
  await page.addInitScript(role => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { userId: 'fixture-user', loginId: 'fixture', name: '검토자', role, status: 'ACTIVE' },
  })), role)
}
const liveUrl = `/plans/review?regionId=${DEMO_REGION}&shiftId=${encodeURIComponent(DEMO_SHIFT)}`
async function mockApi(page: Page, options: { role?: string; planStatus?: number; stationStatus?: number; task?: Task; plan?: Plan; stations?: Station[]; decisionStatus?: number } = {}) {
  await login(page, options.role)
  let currentPlan = structuredClone(options.plan ?? demoPlan)
  const currentTask = structuredClone(options.task ?? demoTask(currentPlan))
  const calls: { path: string; method: string; body: unknown }[] = []
  await page.route(/^http:\/\/127\.0\.0\.1:5185\/api\//, async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname
    calls.push({ path, method: request.method(), body: request.postDataJSON() })
    if (path.endsWith('/stream')) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
    if (path.endsWith('/approval')) {
      if (options.decisionStatus) return route.fulfill({ status: options.decisionStatus, json: { title: '승인 충돌' } })
      currentPlan = { ...currentPlan, status: request.postDataJSON().decision === 'REJECT' ? 'REJECTED' : 'ACTIVE' }
      currentTask.status = 'DONE'; currentTask.pendingApproval = null
      return route.fulfill({ status: 202, json: { taskId: DEMO_TASK_ID } })
    }
    if (path.includes('/ai/tasks/')) return route.fulfill({ json: currentTask })
    if (path.endsWith('/plan')) return route.fulfill({ status: options.planStatus ?? 200, json: options.planStatus ? { title: '계획 조회 오류' } : currentPlan })
    if (path.endsWith('/stations')) return route.fulfill({ status: options.stationStatus ?? 200, json: options.stationStatus ? { title: '현황 오류' } : options.stations ?? demoStations })
    return route.fulfill({ status: 404, json: { title: '미정의 API' } })
  })
  return calls
}
async function linkTask(page: Page) {
  await page.getByLabel('계획 승인 작업 ID').fill(DEMO_TASK_ID)
  await page.getByRole('button', { name: '승인 작업 연결' }).click()
  await expect(page.getByText('근무조 계획을 검토해 주세요.')).toBeVisible()
}

test('공개 시연: API 호출 없이 경로·상세·빈 트럭·필터를 탐색한다', async ({ page }) => {
  const apiRequests: string[] = []
  page.on('request', r => { if (new URL(r.url()).pathname.startsWith('/api/')) apiRequests.push(r.url()) })
  await page.goto('/plans/review/demo')
  await expect(page.getByText('시연 데이터 · 실제 계획에 반영되지 않습니다')).toBeVisible()
  await expect(page.getByRole('group', { name: '트럭 선택' }).getByRole('button', { name: 'truck-1' })).toBeVisible()
  await page.screenshot({ path: 'test-results/plan-review-desktop.png', fullPage: true })
  await page.getByRole('group', { name: '트럭 선택' }).getByRole('button', { name: 'truck-2' }).click()
  await expect(page.getByRole('heading', { name: '건대입구역' })).toBeVisible()
  await page.getByRole('group', { name: '트럭 선택' }).getByRole('button', { name: 'truck-3' }).click()
  await expect(page.getByText('이 트럭에 배정된 작업이 없습니다.')).toBeVisible()
  await page.getByRole('button', { name: /공사 구간 \(시연\).*ST-303/ }).click()
  await expect(page.getByText('이 거점의 대여소 현황이 제공되지 않았습니다.', { exact: false })).toBeVisible()
  await expect(page.getByRole('heading', { name: '공사 구간 (시연)' })).toBeVisible()
  await page.getByLabel('이름·ID 검색').fill('없는 거점')
  await expect(page.getByText('검색 조건에 맞는 대여소가 없습니다.')).toBeVisible()
  await page.getByLabel('이름·ID 검색').clear()
  await page.getByLabel('위험 분류', { exact: true }).selectOption('OVERFLOW')
  await expect(page.getByText('3 / 8곳', { exact: true })).toBeVisible()
  expect(apiRequests).toEqual([])
})
test('제외 후 새 버전을 다시 검토해야 최종 승인할 수 있다', async ({ page }) => {
  await page.goto('/plans/review/demo')
  const confirm = page.getByRole('checkbox', { name: /현재 v1의/ })
  await confirm.check()
  await page.getByRole('checkbox', { name: '성수역 2번 출구 제외', exact: true }).check()
  await expect(confirm).not.toBeChecked()
  await expect(page.getByRole('button', { name: '현재 안 최종 승인' })).toBeDisabled()
  await page.getByRole('button', { name: '제외 후 새 안 요청' }).click()
  const newConfirm = page.getByRole('checkbox', { name: /현재 v2의/ })
  await expect(newConfirm).toBeVisible()
  await expect(newConfirm).not.toBeChecked()
  await expect(page.getByRole('button', { name: '현재 안 최종 승인' })).toBeDisabled()
  await expect(page.getByText('검토 대기', { exact: true })).toBeVisible()
  await newConfirm.check()
  await page.getByRole('button', { name: '현재 안 최종 승인' }).click()
  await expect(page.getByText('운영 중', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '시연 초기화' }).click()
  await expect(page.getByRole('checkbox', { name: /현재 v1의/ })).toBeVisible()
  await page.getByRole('button', { name: '현재 안 거절' }).click()
  await expect(page.getByText('거절됨', { exact: true })).toBeVisible()
})
test('실제 모드: 명세의 공개 API·승인 ID로 한 번만 결정한다', async ({ page }) => {
  const calls = await mockApi(page)
  await page.goto(liveUrl)
  await linkTask(page)
  await page.getByRole('checkbox', { name: /현재 v1의/ }).check()
  await page.getByLabel('검토 의견').fill('경로 검토 완료')
  await page.getByRole('button', { name: '현재 안 최종 승인' }).dblclick()
  await expect(page.getByText('운영 중', { exact: true })).toBeVisible()
  const decisions = calls.filter(c => c.method === 'POST')
  expect(decisions).toHaveLength(1)
  expect(decisions[0]!.body).toEqual({ approvalId: demoTask().pendingApproval!.approvalId, decision: 'APPROVE', comment: '경로 검토 완료' })
  expect(calls.every(c => c.path.startsWith('/api/'))).toBe(true)
  expect(calls.some(c => c.path.endsWith('/stream'))).toBe(true)
})
test('책임자는 조회 전용, 기사는 전체 계획을 요청하지 않는다', async ({ page }) => {
  await mockApi(page, { role: 'EXECUTIVE' })
  await page.goto(liveUrl)
  await expect(page.getByText('조회 전용', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '현재 안 최종 승인' })).toHaveCount(0)
  const driver = await page.context().newPage()
  const calls = await mockApi(driver, { role: 'DRIVER' })
  await driver.goto(liveUrl)
  await expect(driver.getByRole('heading', { name: '관제·책임자용 계획 검토 화면입니다' })).toBeVisible()
  expect(calls).toEqual([])
})
test('401은 로그인으로 이동하고 404는 계획 없음으로 표시한다', async ({ page }) => {
  await mockApi(page, { planStatus: 401 })
  await page.goto(liveUrl)
  await expect(page).toHaveURL(/\/login/)
  const missing = await page.context().newPage()
  await mockApi(missing, { planStatus: 404 })
  await missing.goto(liveUrl)
  await expect(missing.getByRole('heading', { name: '이 근무조의 계획이 아직 없습니다' })).toBeVisible()
  await expect(missing.getByText('시연 데이터 · 실제 계획에 반영되지 않습니다')).toHaveCount(0)
})
test('서버 오류는 가짜 계획으로 대체하지 않으며 재조회할 수 있다', async ({ page }) => {
  const calls = await mockApi(page, { planStatus: 500 })
  await page.goto(liveUrl)
  await expect(page.getByRole('heading', { name: '계획을 불러오지 못했습니다' })).toBeVisible()
  await expect(page.getByText('truck-1', { exact: true })).toHaveCount(0)
  const count = calls.filter(c => c.path.endsWith('/plan')).length
  await page.getByRole('button', { name: '다시 시도' }).click()
  await expect.poll(() => calls.filter(c => c.path.endsWith('/plan')).length).toBe(count + 1)
})
test('선택 필드 누락·예측 0과 현황 실패를 정직하게 표시한다', async ({ page }) => {
  const plan = structuredClone(demoPlan)
  delete plan.routes[0]!.capacity; delete plan.routes[0]!.startLoad
  delete plan.routes[0]!.stops[0]!.loadAfter; delete plan.routes[0]!.stops[0]!.eta
  await mockApi(page, { plan })
  await page.goto(liveUrl)
  await expect(page.locator('.route-meta')).toContainText('용량 미제공대')
  await expect(page.locator('.route-panel tbody tr').first()).toContainText('미제공 / 미제공대')
  await page.locator('.route-panel').getByRole('button', { name: '성수동 주민센터 상세' }).click()
  await expect(page.locator('.detail .facts div').filter({ hasText: '3시간 후 예상 재고' }).locator('dd')).toHaveText('0')
  await expect(page.locator('.detail .facts div').filter({ hasText: '예상 빈 대여소 시각' }).locator('dd')).toHaveText('미제공')
  const degraded = await page.context().newPage()
  await mockApi(degraded, { stationStatus: 503 })
  await degraded.goto(liveUrl)
  await expect(degraded.getByText('대여소 현황 조회 실패:', { exact: false })).toBeVisible()
  await expect(degraded.getByRole('group', { name: '트럭 선택' }).getByRole('button', { name: 'truck-1' })).toBeVisible()
})
test('다른 계획의 카드·오래된 결정은 승인할 수 없다', async ({ page }) => {
  const task = demoTask(); task.pendingApproval!.planId = 'another-plan'
  const calls = await mockApi(page, { task })
  await page.goto(liveUrl); await linkTask(page)
  await expect(page.getByTestId('decision-block')).toContainText('승인 카드와 조회한 계획이 다릅니다')
  await expect(page.getByRole('button', { name: '현재 안 거절' })).toBeDisabled()
  expect(calls.filter(c => c.method === 'POST')).toHaveLength(0)
  const stale = await page.context().newPage()
  const staleCalls = await mockApi(stale, { decisionStatus: 409 })
  await stale.goto(liveUrl); await linkTask(stale)
  await stale.getByRole('button', { name: '현재 안 거절' }).click()
  await expect(stale.getByText('이미 처리됐거나 오래된 승인 요청입니다.', { exact: false })).toBeVisible()
  await expect(stale.getByRole('button', { name: '현재 안 거절' })).toBeDisabled()
  expect(staleCalls.filter(c => c.method === 'POST')).toHaveLength(1)
})
test('제외 요청 202로 확정하지 않고 SSE의 새 후보를 재조회한다', async ({ page }) => {
  await login(page)
  let plan = structuredClone(demoPlan)
  let task = demoTask()
  let body: unknown
  let requested = false
  let publishCandidate = false
  await page.route(/^http:\/\/127\.0\.0\.1:5185\/api\//, async route => {
    const path = new URL(route.request().url()).pathname
    if (path.endsWith('/approval')) {
      body = route.request().postDataJSON(); requested = true
      task = { ...task, status: 'RUNNING', pendingApproval: null }
      return route.fulfill({ status: 202, json: { taskId: DEMO_TASK_ID } })
    }
    if (path.endsWith('/stream')) {
      if (!requested || !publishCandidate) return route.fulfill({ contentType: 'text/event-stream', body: ': heartbeat\n\n' })
      plan = { ...plan, planId: 'new-candidate', version: 2, status: 'PROPOSED' }
      task = demoTask(plan); task.pendingApproval!.approvalId = '432799fb-cc9e-40f1-8c1c-b5e0d22e5a25'
      return route.fulfill({ contentType: 'text/event-stream', body: `data: ${JSON.stringify({ taskId: DEMO_TASK_ID, seq: 1, type: 'APPROVAL_REQUIRED', occurredAt: new Date().toISOString(), approval: task.pendingApproval })}\n\n` })
    }
    if (path.includes('/ai/tasks/')) return route.fulfill({ json: task })
    return route.fulfill({ json: path.endsWith('/plan') ? plan : demoStations })
  })
  await page.goto(liveUrl); await linkTask(page)
  await page.getByRole('checkbox', { name: '성수역 2번 출구 제외', exact: true }).check()
  await page.getByRole('button', { name: '제외 후 새 안 요청' }).click()
  await expect(page.getByText('처리 중', { exact: true })).toBeVisible()
  await expect(page.getByText('운영 중', { exact: true })).toHaveCount(0)
  expect(body).toEqual({ approvalId: demoTask().pendingApproval!.approvalId, decision: 'APPROVE', excludeStationIds: ['ST-101'] })
  publishCandidate = true
  const confirm = page.getByRole('checkbox', { name: /현재 v2의/ })
  await expect(confirm).toBeVisible()
  await expect(confirm).not.toBeChecked()
  await expect(page.getByRole('button', { name: '현재 안 최종 승인' })).toBeDisabled()
})
test('승인 응답이 유실돼도 같은 승인 ID를 다시 보내지 않는다', async ({ page }) => {
  const calls = await mockApi(page)
  let posts = 0
  await page.route('**/api/ai/tasks/*/approval', async route => { posts++; await route.abort('failed') })
  await page.goto(liveUrl); await linkTask(page)
  await page.getByRole('button', { name: '현재 안 거절' }).click()
  await expect(page.getByText('전송 결과를 작업에서 확인한 뒤 진행하세요.', { exact: false })).toBeVisible()
  await expect(page.getByRole('button', { name: '현재 안 거절' })).toBeDisabled()
  await page.getByRole('button', { name: '최신 상태 확인' }).click()
  await expect(page.getByRole('button', { name: '현재 안 거절' })).toBeDisabled()
  expect(posts).toBe(1)
  expect(calls.some(c => c.path.endsWith('/plan'))).toBe(true)
})
test('시연에서 실제 모드로 전환하면 시연 계획·승인 상태를 지운다', async ({ page }) => {
  await mockApi(page, { planStatus: 403 })
  await page.goto('/plans/review/demo')
  await page.getByRole('checkbox', { name: /현재 v1의/ }).check()
  await page.getByRole('link', { name: '실제 계획 조회 →' }).click()
  await expect(page.getByRole('heading', { name: '검토할 근무조를 조회하세요' })).toBeVisible()
  await expect(page.locator('.plan-overview')).toHaveCount(0)
  await page.getByLabel('근무조 ID').fill(DEMO_SHIFT)
  await page.getByRole('button', { name: '계획 조회', exact: true }).click()
  await expect(page.getByRole('heading', { name: '계획을 불러오지 못했습니다' })).toBeVisible()
  await expect(page.locator('.plan-overview')).toHaveCount(0)
})
test('늦게 도착한 이전 근무조 응답이 새 조회를 덮어쓰지 않는다', async ({ page }) => {
  await mockApi(page)
  let release: () => void = () => {}
  let started = false
  const wait = new Promise<void>(resolve => { release = resolve })
  await page.route('**/api/shifts/old-shift/plan', async route => {
    started = true; await wait
    await route.fulfill({ json: { ...demoPlan, shiftId: 'old-shift', planId: 'old-plan', version: 99 } }).catch(() => {})
  })
  await page.goto('/plans/review')
  await page.getByLabel('근무조 ID').fill('old-shift')
  await page.getByRole('button', { name: '계획 조회', exact: true }).click()
  await expect.poll(() => started).toBe(true)
  await page.getByLabel('근무조 ID').fill(DEMO_SHIFT)
  await page.getByRole('button', { name: '계획 조회', exact: true }).click()
  await expect(page.getByRole('checkbox', { name: /현재 v1의/ })).toBeVisible()
  release()
  await expect(page.locator('.plan-id')).toHaveText('계획 ID: demo-plan-001-v1')
})
test('모바일에서 전체 페이지가 가로로 넘치지 않고 경로표를 탐색할 수 있다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/plans/review/demo')
  await expect(page.getByRole('group', { name: '트럭 선택' }).getByRole('button', { name: 'truck-1' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await page.getByRole('group', { name: '트럭 선택' }).getByRole('button', { name: 'truck-2' }).click()
  await expect(page.getByRole('heading', { name: '건대입구역' })).toBeVisible()
  await page.screenshot({ path: 'test-results/plan-review-mobile.png', fullPage: true })
})

test('지도 마커·경로표·방문 번호·상세가 같은 거점을 선택한다', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', e => errors.push(e.message))
  await page.goto('/plans/review/demo')
  const map = page.getByRole('region', { name: '대여소와 방문 순서', exact: true })
  await expect(map).toHaveAttribute('data-map-state', 'ready')
  await expect(map.locator('.map-station-marker')).toHaveCount(8)
  await map.getByRole('button', { name: '전체 거점 보기', exact: true }).click()
  await expect.poll(() => map.evaluate(el => {
    const bounds = el.querySelector('.map-canvas')!.getBoundingClientRect()
    return [...el.querySelectorAll('.map-station-marker')].every(marker => {
      const r = marker.getBoundingClientRect()
      return r.left >= bounds.left && r.right <= bounds.right && r.top >= bounds.top && r.bottom <= bounds.bottom
    })
  })).toBe(true)
  const marker = map.getByRole('button', { name: '지도에서 서울숲 입구 선택', exact: true })
  await marker.focus(); await marker.press('Enter')
  await expect(marker).toHaveAttribute('aria-pressed', 'true')
  await expect(marker).toBeFocused()
  await expect(page.getByRole('heading', { name: '서울숲 입구', exact: true })).toBeVisible()
  await expect(map.locator('.map-selection')).toContainText('서울숲 입구')
  await map.getByRole('combobox', { name: '지도 트럭 선택' }).selectOption('truck-2')
  await expect(page.getByRole('button', { name: 'truck-2 3곳', exact: false })).toHaveAttribute('aria-pressed', 'true')
  await expect(map.getByRole('button', { name: 'truck-2 1번 건대입구역 선택' })).toBeVisible()
  await expect(map.getByRole('button', { name: '지도에서 건대입구역 선택', exact: true }).locator('span')).toHaveText('1')
  await map.getByRole('button', { name: 'truck-2 2번 어린이대공원 정문 선택' }).click()
  await expect(page.getByRole('heading', { name: '어린이대공원 정문', exact: true })).toBeVisible()
  await expect(map.getByRole('button', { name: '지도에서 어린이대공원 정문 선택', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.locator('.route-panel').getByRole('button', { name: '화양사거리 상세' }).click()
  await expect(map.getByRole('button', { name: '지도에서 화양사거리 선택', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await expect(map.getByText('점선은 방문 순서 연결선입니다.', { exact: true })).toBeVisible()
  await map.screenshot({ path: 'test-results/plan-map-desktop.png' })
  expect(errors).toEqual([])
})

test('지도 검색·필터와 제외 선택·새 후보의 방문 번호를 갱신한다', async ({ page }) => {
  await page.goto('/plans/review/demo')
  const map = page.getByRole('region', { name: '대여소와 방문 순서', exact: true })
  await expect(map.locator('.map-station-marker')).toHaveCount(8)
  await page.getByLabel('위험 분류', { exact: true }).selectOption('OVERFLOW')
  await expect(map.locator('.map-station-marker')).toHaveCount(3)
  await page.getByLabel('이름·ID 검색').fill('건대')
  await expect(map.locator('.map-station-marker')).toHaveCount(1)
  await page.getByLabel('이름·ID 검색').clear()
  await page.getByLabel('위험 분류', { exact: true }).selectOption('ALL')
  const original = map.getByRole('button', { name: '지도에서 성수역 2번 출구 선택', exact: true })
  await page.getByRole('checkbox', { name: '성수역 2번 출구 제외', exact: true }).check()
  await expect(original).toHaveClass(/is-excluded/)
  await expect(original.locator('span')).toHaveText('1')
  await page.getByRole('button', { name: '제외 후 새 안 요청' }).click()
  await expect(page.getByRole('checkbox', { name: /현재 v2의/ })).toBeVisible()
  await expect(original).not.toHaveClass(/is-excluded/)
  await expect(original).not.toHaveClass(/on-route/)
})

test('좌표가 없는 거점의 위치를 만들지 않고 지도 없는 환경에서도 검토할 수 있다', async ({ page }) => {
  await page.goto('/plans/review/demo')
  const map = page.getByRole('region', { name: '대여소와 방문 순서', exact: true })
  await expect(map.locator('.map-station-marker')).toHaveCount(8)
  await page.getByRole('button', { name: /공사 구간 \(시연\).*ST-303/ }).click()
  await expect(map.getByText('선택한 거점의 좌표가 없어 지도 위치를 표시하지 않습니다.', { exact: false })).toBeVisible()
  await expect(map.getByRole('button', { name: '지도에서 공사 구간 (시연) 선택', exact: true })).toHaveCount(0)
  const blocked = await page.context().newPage()
  await blocked.addInitScript(() => {
    const original = HTMLCanvasElement.prototype.getContext
    HTMLCanvasElement.prototype.getContext = function(kind: string, ...args: unknown[]) {
      if (kind === 'webgl2' || kind === 'webgl') return null
      return original.call(this, kind, ...args)
    } as typeof original
  })
  await blocked.goto('/plans/review/demo')
  await expect(blocked.locator('.plan-map-panel')).toHaveAttribute('data-map-state', 'failed')
  await expect(blocked.getByText('이 환경에서 지도를 표시할 수 없습니다.', { exact: false })).toBeVisible()
  await blocked.getByRole('checkbox', { name: /현재 v1의/ }).check()
  await expect(blocked.getByRole('button', { name: '현재 안 최종 승인' })).toBeEnabled()
})

test('배경지도 실패 시 마커를 유지하고 재시도로 복구한다', async ({ page }) => {
  let fail = true
  await page.route('https://tiles.openfreemap.org/**', route => fail
    ? route.fulfill({ status: 503, body: 'unavailable' })
    : route.fulfill({ json: { version: 8, sources: {}, layers: [] } }))
  await page.goto('/plans/review/demo')
  const map = page.getByRole('region', { name: '대여소와 방문 순서', exact: true })
  await expect(map.getByText('배경지도 일부를 불러오지 못했습니다.', { exact: false })).toBeVisible()
  await expect(map.locator('.map-station-marker')).toHaveCount(8)
  fail = false
  await map.getByRole('button', { name: '배경지도 재시도' }).click()
  await expect(map.getByRole('button', { name: '배경지도 재시도' })).toHaveCount(0)
  await expect(map.locator('.map-station-marker')).toHaveCount(8)
})

test('계획 없음과 잘못된 좌표를 구분하고 현황 200곳을 탐색한다', async ({ page }) => {
  const stations = Array.from({ length: 200 }, (_, i) => ({ ...demoStations[i % 8]!, stationId: `load-${i}`, name: `부하 검증 ${i}`, lat: 37.54 + (i % 20) * .0005, lon: 127.04 + Math.floor(i / 20) * .0005 }))
  await mockApi(page, { planStatus: 404, stations })
  await page.goto(liveUrl)
  const map = page.getByRole('region', { name: '대여소와 방문 순서', exact: true })
  await expect(map.locator('.map-station-marker')).toHaveCount(200)
  await map.getByRole('button', { name: '전체 거점 보기' }).click()
  await page.getByLabel('이름·ID 검색').fill('load-199')
  await expect(map.locator('.map-station-marker')).toHaveCount(1)
  await map.getByRole('button', { name: '지도에서 부하 검증 199 선택' }).click()
  await expect(map.locator('.map-selection')).toContainText('부하 검증 199')
  await expect(page.getByRole('button', { name: '현재 안 최종 승인' })).toHaveCount(0)
  const invalid = await page.context().newPage()
  await mockApi(invalid, { stations: [{ ...demoStations[0]!, lat: 100 }] })
  await invalid.goto(liveUrl)
  await expect(invalid.getByText('표시할 대여소 좌표가 없습니다.', { exact: false })).toBeVisible()
  await expect(invalid.locator('.map-station-marker')).toHaveCount(0)
})

test('모바일 지도 선택·확대와 목록 이동에서도 가로 넘침이 없다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/plans/review/demo')
  const map = page.getByRole('region', { name: '대여소와 방문 순서', exact: true })
  await expect(map.locator('.map-station-marker')).toHaveCount(8)
  await map.getByRole('combobox', { name: '지도 트럭 선택' }).selectOption('truck-2')
  await map.getByRole('button', { name: '선택 트럭 보기', exact: true }).click()
  await map.getByRole('button', { name: 'truck-2 1번 건대입구역 선택' }).click()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
  await map.screenshot({ path: 'test-results/plan-map-mobile.png' })
})

test('계정 비활성화 401 응답을 받으면 지도와 승인 화면을 종료한다', async ({ page }) => {
  await mockApi(page)
  await page.goto(liveUrl)
  await expect(page.locator('.map-station-marker')).toHaveCount(8)
  await page.route('**/api/shifts/*/plan', route => route.fulfill({ status: 401, json: { code: 'UNAUTHORIZED', detail: '계정이 활성 상태가 아니다. 다시 로그인한다' } }))
  await page.getByRole('button', { name: '최신 상태 확인' }).click()
  await expect(page).toHaveURL(/\/login/)
  await expect(page.locator('.map-station-marker')).toHaveCount(0)
  await expect(page.getByRole('button', { name: '현재 안 최종 승인' })).toHaveCount(0)
})
