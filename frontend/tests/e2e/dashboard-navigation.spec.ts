import { expect, test, type Page } from '@playwright/test'
import { mockBoard, mockDriver } from './fixtures/board'

const nav = (page: Page) => page.getByRole('navigation', { name: '대시보드 화면' })

async function openDashboard(page: Page) {
  await page.addInitScript(() => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { userId: 'fixture-manager', loginId: 'fixture', name: '검토자', role: 'MANAGER', status: 'ACTIVE' },
  })))
  await page.route('**/api/ai/tasks?**', route => route.fulfill({ json: { items: [] } }))
  // 평면 지도 배경은 외부(OpenFreeMap)에서 받는다. 시험은 네트워크에 기대지 않게 빈 스타일을 준다
  await page.context().route('https://tiles.openfreemap.org/**', route => route.fulfill({ json: {
    version: 8, sources: {}, layers: [{ id: 'test-background', type: 'background', paint: { 'background-color': '#e8eef4' } }],
  } }))
  await mockBoard(page)
  await page.goto('/dashboard')
  // 개발 서버가 대시보드(3D 뷰·지도)를 처음 컴파일하면 5초를 넘길 수 있다. CI는 늘 캐시 없이 돈다.
  await expect(page.getByRole('navigation', { name: '대시보드 화면' })).toBeVisible({ timeout: 20_000 })
}

/** 들어오면 평면 지도가 보인다. 3D 관제 뷰를 보는 시험은 탭으로 연다 */
async function show3d(page: Page) {
  await page.getByRole('tab', { name: '3D 관제 뷰' }).click()
  await expect(page.locator('svg.iso')).toBeVisible()
}

test('초기 관제 화면만 보이고 적재 현황은 3D 지도 안에만 있다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openDashboard(page)

  await expect(nav(page).getByRole('button', { name: '관제 뷰' })).toHaveAttribute('aria-pressed', 'true')
  // 색 그림자를 쓰지 않는다 (소스 전체는 tests/style-rules.test.ts가 본다)
  await expect(nav(page).getByRole('button', { name: '관제 뷰' })).toHaveCSS('box-shadow', 'none')
  await expect(page.locator('.brand-mark')).toHaveCSS('box-shadow', 'none')
  await expect(nav(page).getByRole('button', { name: '트럭 적재' })).toHaveCount(0)
  await expect(page.locator('#view')).toBeVisible()
  // 처음에는 실제 지도(평면)가 보이고, 적재 칸은 3D 뷰에만 있다
  await expect(page.getByRole('tab', { name: '평면 지도' })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('#view .flat-map .station-map')).toHaveAttribute('data-map-state', 'ready')
  await expect(page.locator('.map-truck-load')).toHaveCount(0)
  await show3d(page)
  for (const id of ['trend', 'agent', 'routes', 'dropped']) {
    await expect(page.locator(`#${id}`)).toBeHidden()
  }
  await expect(page.locator('.scene > .map-truck-load')).toBeVisible()
  await expect(page.locator('.map-truck-load')).toHaveCount(1)
  await expect(page.locator('.map-truck-load')).toHaveCSS('position', 'absolute')
  const scene = await page.locator('.scene').boundingBox()
  const load = await page.locator('.map-truck-load').boundingBox()
  expect(scene).not.toBeNull()
  expect(load).not.toBeNull()
  expect(load!.x).toBeGreaterThanOrEqual(scene!.x)
  expect(load!.y).toBeGreaterThanOrEqual(scene!.y)
  expect(load!.x + load!.width).toBeLessThanOrEqual(scene!.x + scene!.width)
  expect(load!.x).toBeGreaterThan(scene!.x + scene!.width / 2)
  expect(load!.y).toBeLessThan(scene!.y + 100)
  await page.screenshot({ path: 'test-results/dashboard-1440-3d.png', fullPage: true })

  // 적재 칸에는 트럭 버튼이 없다. 위 트럭 버튼으로 고른 트럭을 보여 준다
  const loadPanel = page.locator('.map-truck-load')
  await expect(loadPanel.getByRole('button')).toHaveCount(0)
  await expect(loadPanel.locator('.load-truck')).toHaveText('TR-03')
  await page.getByRole('group', { name: '트럭 경로 골라 보기' }).getByRole('button', { name: 'TR-04' }).click()
  await expect(loadPanel.locator('.load-truck')).toHaveText('TR-04')
  await expect(loadPanel).toContainText('12 / 15대')
  // 적재 칸은 켜고 끈다
  const loadSwitch = page.getByRole('switch', { name: '트럭 적재' })
  await expect(loadSwitch).toHaveAttribute('aria-checked', 'true')
  await loadSwitch.click()
  await expect(page.locator('.map-truck-load')).toHaveCount(0)
  await loadSwitch.click()
  await expect(page.locator('.map-truck-load')).toBeVisible()
  // 운행 상세는 관제 뷰 아래 칸에 트럭마다 있다
  const ops = page.getByRole('region', { name: '운행 상세' })
  await expect(ops.locator('.ops-list li')).toHaveCount(3)
  await expect(ops).toContainText('다음 정류장')
  expect((await ops.boundingBox())!.y).toBeGreaterThan(scene!.y + scene!.height)
  await expect(ops.locator('li', { hasText: 'TR-04' }).getByRole('button', { name: '전체 보기' })).toBeVisible()

  await page.getByRole('tab', { name: '평면 지도' }).click()
  await expect(page.locator('#view .flat-map')).toBeVisible()
  await expect(page.locator('.map-truck-load')).toHaveCount(0)
  await page.getByRole('tab', { name: '3D 관제 뷰' }).click()
  await expect(page.locator('.scene > .map-truck-load')).toBeVisible()
})

test('권역 상태 색 버튼은 그 위험 종류의 대여소만 지도·재고 현황에 보인다 (다시 누르면 전부)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openDashboard(page)
  await show3d(page)
  const stock = page.locator('#view .stock')
  // 권역 상태는 재고 현황 목록 위에 있다
  const zone = (await stock.locator('.zone-status').boundingBox())!
  expect(zone.y).toBeLessThan((await stock.locator('.stock-list').boundingBox())!.y)

  await expect(page.locator('svg.iso .col')).toHaveCount(18)
  const empty = stock.getByRole('button', { name: '빈 대여소 위험 6곳만 보기' })
  await empty.click()
  await expect(empty).toHaveAttribute('aria-pressed', 'true')
  await expect(page.locator('svg.iso .col')).toHaveCount(6)
  await expect(stock.locator('.stock-list li')).toHaveCount(6)
  for (const word of await stock.locator('.stock-list .risk').allTextContents()) expect(word).toBe('부족')
  // 평면 지도도 같다
  await page.getByRole('tab', { name: '평면 지도' }).click()
  await expect(page.locator('.flat-map .station')).toHaveCount(6)
  await page.getByRole('tab', { name: '3D 관제 뷰' }).click()

  await stock.getByRole('button', { name: '정상 7곳만 보기' }).click()
  await expect(page.locator('svg.iso .col')).toHaveCount(7)
  for (const word of await stock.locator('.stock-list .risk').allTextContents()) expect(word).toBe('정상')
  await stock.getByRole('button', { name: '정상 7곳만 보기' }).click()
  await expect(page.locator('svg.iso .col')).toHaveCount(18)
})

test('지도는 실제 거리 비율로 그리고 축척을 적는다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openDashboard(page)
  await show3d(page)
  const map = page.locator('svg.iso')
  const grid3d = async () => Number(await map.getAttribute('data-grid-m'))
  // 3D: 멀리서는 넓은 칸, 확대하면 100m 칸까지 촘촘해지고 그 값을 확대 버튼 아래에 적는다
  const far = await grid3d()
  await expect(page.locator('.zoom-ctl .grid-scale')).toContainText(far >= 1000 ? `${far / 1000}km` : `${far}m`)
  const zoomIn = page.getByRole('button', { name: '확대', exact: true })
  while ((await grid3d()) > 100 && (await zoomIn.isEnabled())) await zoomIn.click()
  expect(await grid3d()).toBe(100)
  await expect(page.locator('.zoom-ctl .grid-scale')).toContainText('100m')
  expect(far).toBeGreaterThan(100)
  // 평면 지도: 실제 지도(MapLibre)의 축척 막대
  await page.getByRole('tab', { name: '평면 지도' }).click()
  await expect(page.locator('.flat-map .station-map')).toHaveAttribute('data-map-state', 'ready')
  await expect(page.locator('.flat-map .maplibregl-ctrl-scale')).toHaveText(/^\d+\s?(m|km)$/)
})

test('평면 지도를 끌어 대여소가 위쪽 버튼 밑으로 가도 버튼이 위에 남는다', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 })
  await openDashboard(page)
  await expect(page.locator('.flat-map .station-map')).toHaveAttribute('data-map-state', 'ready')
  // 지도 윗변을 고정 상단바(60px) 바로 아래로
  await page.locator('.scene').evaluate((e) => window.scrollBy(0, e.getBoundingClientRect().top - 80))
  const tab = page.getByRole('tab', { name: '3D 관제 뷰' })
  const t = (await tab.boundingBox())!
  const m = (await page.locator('.flat-map .station').first().boundingBox())!
  const [tx, ty] = [t.x + t.width / 2, t.y + t.height / 2]
  const [mx, my] = [m.x + m.width / 2, m.y + m.height / 2]
  // 대여소 하나를 탭 한가운데로 끌어 온다 (끄는 시작점은 지도 가운데)
  const start = (await page.locator('.flat-map .station-map').boundingBox())!
  const [sx, sy] = [start.x + start.width / 2, start.y + 300]
  await page.mouse.move(sx, sy)
  await page.mouse.down()
  await page.mouse.move(sx + (tx - mx), sy + (ty - my), { steps: 10 })
  await page.mouse.up()
  const onTop = await page.evaluate(([x, y]) => !!document.elementFromPoint(x!, y!)?.closest('[role="tab"]'), [tx, ty])
  expect(onTop).toBe(true)
  await tab.click()
  await expect(page.locator('svg.iso')).toBeVisible()
})

test('3D 관제 뷰: 휠로 확대·축소, 끌어서 이동(끈 뒤 클릭은 선택이 아님), 버튼·전체 보기', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openDashboard(page)
  await show3d(page)
  const map = page.locator('svg.iso')
  const zoom = async () => Number(await map.getAttribute('data-zoom'))
  await expect(map).toHaveAttribute('data-zoom', '1.00')
  // 멀리서는 기둥만 그린다. 거치대 상자는 고른 대여소(처음엔 가장 급한 곳) 하나뿐
  await expect(page.locator('svg.iso .col .ghost')).toHaveCount(1)

  const box = (await map.boundingBox())!
  const [cx, cy] = [box.x + box.width / 2, box.y + box.height / 2]
  await page.mouse.move(cx, cy)
  await page.mouse.wheel(0, -300)
  await page.mouse.wheel(0, -300)
  await expect.poll(zoom).toBeGreaterThan(1.8)
  // 확대하면 모든 대여소의 거치대 상자를 그린다
  await expect(page.locator('svg.iso .col .ghost')).toHaveCount(18)

  // 끌면 보기가 옮겨지고, 끈 뒤의 클릭은 대여소를 고르지 않는다
  const detail = page.locator('#view .stock .w-detail')
  const picked = await detail.textContent()
  const before = await map.getAttribute('viewBox')
  await page.mouse.down()
  await page.mouse.move(cx + 140, cy + 70, { steps: 6 })
  await page.mouse.up()
  expect(await map.getAttribute('viewBox')).not.toBe(before)
  await expect(detail).toHaveText(picked!)

  await page.getByRole('button', { name: '구역 전체 보기' }).click()
  await expect(map).toHaveAttribute('data-zoom', '1.00')
  await expect(page.getByRole('button', { name: '구역 전체 보기' })).toBeDisabled()
  await page.getByRole('button', { name: '확대', exact: true }).click()
  await expect(map).toHaveAttribute('data-zoom', '1.60')
  await page.getByRole('button', { name: '축소', exact: true }).click()
  await expect(map).toHaveAttribute('data-zoom', '1.00')
  await page.screenshot({ path: 'test-results/dashboard-3d-zoom.png' })
})

test('관제 뷰에서 고른 대여소는 지도 바로 옆 재고 현황 칸에 보인다 (#74)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openDashboard(page)
  const detail = page.locator('#view .stock .w-detail')
  await expect(page.locator('.w-detail')).toHaveCount(1)  // 지도 아래 따로 뜨던 창은 없다
  await expect(detail).toContainText('성수역 3번출구')

  await page.getByRole('button', { name: '서울숲 정문 재고 28/30' }).click()
  await expect(detail).toContainText('서울숲 정문')
  await expect(detail).toContainText('ST-511')
  const scene = (await page.locator('.scene').boundingBox())!
  const box = (await detail.boundingBox())!
  expect(box.x).toBeGreaterThanOrEqual(scene.x + scene.width - 1)  // 지도 오른쪽 바로 옆
  expect(box.y).toBeLessThan(scene.y + 60)  // 지도 윗줄에 맞춰
  expect(box.y + box.height).toBeLessThanOrEqual(900)  // 스크롤 없이 보인다
  expect((await page.locator('.stock').boundingBox())!.height).toBeCloseTo(scene.height, 0)  // 칸이 지도 높이를 늘리지 않는다
  await page.screenshot({ path: 'test-results/dashboard-1440-selected.png' })

  // 목록에서 골라도 같은 칸에 보이고, 선택을 풀면 재고 현황만 남는다
  await page.locator('.stock-list').getByRole('button', { name: /구의역 1번출구/ }).click()
  await expect(detail).toContainText('ST-561')
  await detail.getByRole('button', { name: '대여소 선택 해제' }).click()
  await expect(page.locator('.w-detail')).toHaveCount(0)
  await expect(page.locator('.stock-list')).toBeVisible()
})

test('메뉴마다 해당 화면만 보이고 경로 선택은 경로 화면에 머문다', async ({ page }) => {
  await openDashboard(page)
  const screens = [
    { button: '재고 예측', id: 'trend', heading: '3시간 뒤 예측 재고' },
    { button: '에이전트 대화', id: 'agent', heading: '에이전트 대화' },
    { button: '트럭별 경로', id: 'routes', heading: '트럭별 경로' },
    { button: '빠진 대여소', id: 'dropped', heading: '이번 계획에서 빠진 대여소' },
  ] as const
  for (const screen of screens) {
    await nav(page).getByRole('button', { name: screen.button }).click()
    await expect(page.locator(`#${screen.id}`)).toBeVisible()
    await expect(page.locator(`#${screen.id}`).getByRole('heading', { name: screen.heading })).toBeVisible()
    await expect(nav(page).getByRole('button', { name: screen.button })).toHaveAttribute('aria-pressed', 'true')
    for (const other of ['view', 'trend', 'agent', 'routes', 'dropped'].filter(id => id !== screen.id)) {
      await expect(page.locator(`#${other}`)).toBeHidden()
    }
  }

  await nav(page).getByRole('button', { name: '트럭별 경로' }).click()
  await page.locator('#routes .timeline li').filter({ hasText: '서울숲 정문' }).click()
  await expect(page.locator('#routes')).toBeVisible()
  await expect(page.locator('.w-detail')).toContainText('서울숲 정문')
  await expect(page.locator('#view')).toBeHidden()
})

test('예측 선택과 빠진 대여소 펼침은 독립적이고 지도 이동은 명시적이다', async ({ page }) => {
  await openDashboard(page)
  await nav(page).getByRole('button', { name: '재고 예측' }).click()
  const forecastStation = page.locator('#trend').getByRole('button', { name: /뚝섬역 1번출구 지금 2대/ })
  await forecastStation.click()
  await expect(forecastStation).toHaveClass(/sel/)
  await expect(page.locator('.w-detail')).toContainText('ST-502')

  await nav(page).getByRole('button', { name: '빠진 대여소' }).click()
  const droppedStation = page.locator('#dropped .dropped-list li').filter({ hasText: '강변역 4번출구' })
  await droppedStation.locator('.drop-head').click()
  await expect(droppedStation.locator('.drop-head')).toHaveAttribute('aria-expanded', 'true')
  await expect(droppedStation.locator('.drop-body')).toBeVisible()
  await expect(page.locator('.w-detail')).toHaveCount(0)
  await expect(page.locator('#view')).toBeHidden()

  await nav(page).getByRole('button', { name: '재고 예측' }).click()
  await expect(forecastStation).toHaveClass(/sel/)
  await expect(page.locator('.w-detail')).toContainText('ST-502')
  await nav(page).getByRole('button', { name: '빠진 대여소' }).click()
  await expect(droppedStation.locator('.drop-head')).toHaveAttribute('aria-expanded', 'true')
  await droppedStation.getByRole('button', { name: '지도에서 보기' }).click()
  await expect(page.locator('#view')).toBeVisible()
  await expect(page.locator('.w-detail')).toContainText('ST-563')
  await nav(page).getByRole('button', { name: '재고 예측' }).click()
  await expect(forecastStation).toHaveClass(/sel/)
  await expect(page.locator('.w-detail')).toContainText('ST-502')
})

for (const width of [900, 390]) {
  test(`${width}px에서도 대시보드 메뉴가 보이고 화면 전환이 된다`, async ({ page }) => {
    await page.setViewportSize({ width, height: 850 })
    await openDashboard(page)
    for (const name of ['관제 뷰', '재고 예측', '에이전트 대화', '트럭별 경로', '빠진 대여소']) {
      await expect(nav(page).getByRole('button', { name })).toBeVisible()
    }
    if (width === 390) {
      await show3d(page)
      await page.screenshot({ path: 'test-results/dashboard-390-mobile.png', fullPage: true })
      expect((await page.locator('.map-truck-load').boundingBox())!.width).toBeLessThanOrEqual(150)
    }
    await nav(page).getByRole('button', { name: '빠진 대여소' }).click()
    await expect(page.locator('#dropped')).toBeVisible()
    await expect(page.locator('#view')).toBeHidden()
    await nav(page).getByRole('button', { name: '관제 뷰' }).click()
    await expect(page.locator('#view')).toBeVisible()
  })
}

test('TR-04 기사는 TR-04 시연 경로를 본다', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { userId: 'fixture-driver2', loginId: 'driver2', name: '이기사', role: 'DRIVER', status: 'ACTIVE', truckId: 'TR-04' },
  })))
  await mockDriver(page)
  await page.goto('/driver')

  await expect(page.locator('.bar .truck')).toHaveText('🚚 TR-04')
  await expect(page.getByRole('heading', { name: '건대입구역 2번출구' })).toBeVisible()
  // 적재는 마지막으로 끝낸 정류장(어린이대공원역, 실은 뒤 12대)의 값이다
  await expect(page.locator('.status .load')).toContainText('12/15')
  // 구역 대여소 좌표가 있으면 길찾기 링크다
  await expect(page.getByRole('link', { name: /길안내/ })).toHaveAttribute('href', /link\/to\/.*,37\.5404,127\.0692$/)

  // 운행 중에는 배지만, 정차하면 승인 대기 작업의 기사 카드를 연다 (FR-14)
  await expect(page.locator('.bar .notice')).toHaveText('변경 제안 1')
  await page.getByRole('button', { name: /운행 중/ }).click()
  await expect(page.locator('.proposal .approval')).toContainText('TR-04')
})

test('기사 보고는 근무조·도착 재고·실제 대수를 POST /api/reports로 보낸다', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { userId: 'fixture-driver2', loginId: 'driver2', name: '이기사', role: 'DRIVER', status: 'ACTIVE', truckId: 'TR-04' },
  })))
  const sent = await mockDriver(page)
  await page.goto('/driver')
  await page.getByRole('button', { name: '도착 · 보고' }).click()
  const sheet = page.getByRole('dialog', { name: '정류장 보고' })
  // 실제 내린 대수는 계획 8대에서 하나 줄이고, 도착 재고는 앱 재고(4대)에서 하나 줄인다
  await sheet.getByRole('button', { name: '하나 줄이기', exact: true }).click()
  await sheet.getByRole('button', { name: '도착 재고 하나 줄이기' }).click()
  await sheet.getByRole('button', { name: '완료' }).click()
  await expect(sheet).toHaveCount(0)
  await expect.poll(() => sent.filter((s) => s.path === '/api/reports').length).toBe(1)
  expect(sent[0]!.body).toEqual({
    shiftId: 'N02:2026-10-21:AM', stationId: 'ST-541', actualBikes: 3, loaded: null, unloaded: 7, issue: null, memo: null,
  })

  // 진입 불가는 처리하지 못한 보고라 실은·내린 대수를 보내지 않는다
  await page.getByRole('button', { name: '도착 · 보고' }).click()
  await sheet.getByRole('button', { name: /진입 불가/ }).click()
  await expect(sheet.getByRole('button', { name: '하나 줄이기', exact: true })).toHaveCount(0)
  await sheet.getByRole('button', { name: '완료' }).click()
  await expect.poll(() => sent.filter((s) => s.path === '/api/reports').length).toBe(2)
  expect(sent[1]!.body).toMatchObject({ stationId: 'ST-541', loaded: null, unloaded: null, issue: 'BLOCKED' })
})

test('기사 카드 수락은 그 작업의 승인 API로 보내고 카드를 내린다', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { userId: 'fixture-driver2', loginId: 'driver2', name: '이기사', role: 'DRIVER', status: 'ACTIVE', truckId: 'TR-04' },
  })))
  const sent = await mockDriver(page)
  await page.goto('/driver')
  await page.getByRole('button', { name: /운행 중/ }).click()
  await page.locator('.proposal').getByRole('button', { name: /수락|승인/ }).first().click()
  await expect.poll(() => sent.filter((s) => s.path.endsWith('/approval')).length).toBe(1)
  expect(sent.find((s) => s.path.endsWith('/approval'))).toMatchObject({
    path: '/api/ai/tasks/00000000-0000-4000-8000-000000000301/approval',
    body: { approvalId: '00000000-0000-4000-8000-000000000201', decision: 'APPROVE' },
  })
  await expect(page.locator('.proposal')).toHaveCount(0)
})
