import { expect, test, type Page } from '@playwright/test'

const nav = (page: Page) => page.getByRole('navigation', { name: '대시보드 화면' })

async function openDashboard(page: Page) {
  await page.addInitScript(() => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { userId: 'fixture-manager', loginId: 'fixture', name: '검토자', role: 'MANAGER', status: 'ACTIVE' },
  })))
  await page.route('**/api/ai/tasks?**', route => route.fulfill({ json: { items: [] } }))
  await page.goto('/dashboard')
  // 개발 서버가 대시보드(3D 뷰·지도)를 처음 컴파일하면 5초를 넘길 수 있다. CI는 늘 캐시 없이 돈다.
  await expect(page.getByRole('navigation', { name: '대시보드 화면' })).toBeVisible({ timeout: 20_000 })
}

test('초기 관제 화면만 보이고 적재 현황은 3D 지도 안에만 있다', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openDashboard(page)

  await expect(nav(page).getByRole('button', { name: '관제 뷰' })).toHaveAttribute('aria-pressed', 'true')
  await expect(nav(page).getByRole('button', { name: '트럭 적재' })).toHaveCount(0)
  await expect(page.locator('#view')).toBeVisible()
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

  const loadPanel = page.locator('.map-truck-load')
  await loadPanel.getByRole('button', { name: 'TR-04' }).click()
  await expect(page.getByRole('group', { name: '트럭 경로 골라 보기' }).getByRole('button', { name: 'TR-04' })).toHaveAttribute('aria-pressed', 'true')
  await loadPanel.locator('summary').click()
  await expect(loadPanel.getByText('다음 정류장')).toBeVisible()
  await expect(loadPanel.getByRole('button', { name: '지도에서 이 트럭만 보기' })).toBeVisible()

  await page.getByRole('tab', { name: '평면 지도' }).click()
  await expect(page.locator('#view .flat-map')).toBeVisible()
  await expect(page.locator('.map-truck-load')).toHaveCount(0)
  await page.getByRole('tab', { name: '3D 관제 뷰' }).click()
  await expect(page.locator('.scene > .map-truck-load')).toBeVisible()
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
  await page.goto('/driver')

  await expect(page.locator('.bar .truck')).toHaveText('🚚 TR-04')
  await expect(page.getByRole('heading', { name: '건대입구역 2번출구' })).toBeVisible()
  await expect(page.locator('.status .load')).toContainText('/15')

  await page.getByRole('button', { name: /운행 중/ }).click()
  await expect(page.locator('.proposal .approval')).toContainText('TR-04')
})
