import { test, expect, type Page } from '@playwright/test'
import type { Schemas } from '../../src/shared/api/client'

// 에이전트 추론 로그(관리자): 지난 기록 + 실시간(SSE) 줄, 모델의 선택 이유, 거르기·명령 줄, 역할 막기.
// backend는 page.route로 흉내 낸다 (계약 openapi v0.16 /api/admin/agent-traces, /stream).

type AgentTrace = Schemas['AgentTrace']

const TASK = '5ae7684c-1111-4ab7-a636-94656f049298'
const OTHER = '9b0c2d11-2222-4ab7-a636-94656f049298'
const SHIFT = 'N02:2026-10-22:AM'

const at = (s: number) => `2026-10-22T07:11:${String(s).padStart(2, '0')}+09:00`
const trace = (id: number, kind: AgentTrace['kind'], title: string, extra: Partial<AgentTrace> = {}): AgentTrace => ({
  id, eventId: `00000000-0000-4000-8000-${String(id).padStart(12, '0')}`, taskId: TASK, shiftId: SHIFT, traceId: 't',
  runId: null, source: 'HARNESS', kind, turn: null, title, detail: {}, occurredAt: at(id), receivedAt: at(id), ...extra,
})

const HISTORY: AgentTrace[] = [
  trace(1, 'ROUTE', 'FIELD_REPORT → 모델 경로: 모델이 요청을 해석해 쓸 도구를 고른다', { source: 'GRAPH' }),
  trace(2, 'RUN_START', '모델 경로 시작 · field_report · 도구 10개 중에서 모델이 고른다', {
    detail: { taskType: 'field_report', tools: ['business_request_route_plan'], withheld: [{ name: 'business_get_audit', why: '기사 작업에는 주지 않는다 (NFR-06)' }] },
  }),
  trace(3, 'DECISION', '선택: business_request_route_plan(kind=REPLAN, truck_ids=["TR-03"])', {
    turn: 1,
    detail: { calls: [{ id: 'c1', name: 'business_request_route_plan', args: { kind: 'REPLAN', truck_ids: ['TR-03'] }, rationale: '진입 불가 보고라 ST-1660을 빼고 TR-03만 다시 계산한다' }] },
  }),
  trace(4, 'TOOL_CALL', '→ business_request_route_plan(kind=REPLAN, truck_ids=["TR-03"])', { turn: 1 }),
  trace(5, 'TOOL_RESULT', '← business_request_route_plan 오류 0.40초, 88자: 없는 대여소', { turn: 1, detail: { ok: false } }),
  trace(6, 'GUARD', '인자 검증 거부 1건: 실행·승인 요청 전에 사유를 붙여 모델에 한 번 되돌린다', {
    turn: 2, detail: { rule: 'ARGUMENT_VALIDATION', action: 'RETRY', reasons: ['조회 결과에 없는 대여소 ID ST-9999'] },
  }),
  trace(7, 'RUN_START', '모델 경로 시작 · qa · 도구 8개 중에서 모델이 고른다', { taskId: OTHER, detail: { taskType: 'qa' } }),
]
const LIVE = trace(8, 'DECISION', '선택: business_propose_route_change(candidate_id=C-1, truck_id=TR-03)', {
  turn: 2,
  detail: { calls: [{ id: 'c2', name: 'business_propose_route_change', args: {}, rationale: '바뀐 경로가 MINOR라 기사 수락을 요청한다' }] },
})

async function login(page: Page, role: string) {
  await page.addInitScript((role) => localStorage.setItem('seoul-bike.auth', JSON.stringify({
    token: 'fixture-token', expiresAt: Date.now() + 3_600_000,
    user: { id: 'u', name: '관제 관리자', role, truckId: role === 'DRIVER' ? 'TR-03' : null },
  })), role)
}

async function mockLog(page: Page) {
  const streams: { auth: string | undefined; lastEventId: string | undefined }[] = []
  await page.route('**/api/admin/agent-traces/stream**', (route) => {
    const headers = route.request().headers()
    streams.push({ auth: headers.authorization, lastEventId: headers['last-event-id'] })
    const body = streams.length === 1 ? `: ping\n\nid:8\nevent:DECISION\ndata:${JSON.stringify(LIVE)}\n\n` : ': ping\n\n'
    return route.fulfill({ contentType: 'text/event-stream', body })
  })
  await page.route(/\/api\/admin\/agent-traces(\?.*)?$/, (route) => {
    const taskId = new URL(route.request().url()).searchParams.get('taskId')
    const items = taskId ? HISTORY.filter((t) => t.taskId === taskId) : HISTORY
    return route.fulfill({ json: { items, hasMore: false } })
  })
  // 상단 바 밖의 화면이 부르는 다른 API는 비워 둔다
  await page.route('**/api/ai/tasks?**', (route) => route.fulfill({ json: { items: [], page: 0, size: 20, total: 0 } }))
  return streams
}

test('관리자: 모델이 고른 도구와 그 이유를 지난 기록과 실시간으로 한 줄씩 본다', async ({ page }) => {
  await login(page, 'MANAGER')
  const streams = await mockLog(page)
  await page.goto('/admin/agent-log')

  await expect(page.getByRole('heading', { name: '에이전트 추론 로그' })).toBeVisible()
  await expect(page.getByRole('link', { name: /에이전트 로그/ })).toHaveClass(/router-link-active/)
  const log = page.getByRole('log', { name: '에이전트 추론 로그' })
  // 판단 줄 아래에 도구마다 모델의 선택 이유가 붙는다
  await expect(log.getByText('진입 불가 보고라 ST-1660을 빼고 TR-03만 다시 계산한다')).toBeVisible()
  await expect(log.getByText('조회 결과에 없는 대여소 ID ST-9999')).toBeVisible()
  // SSE로 들어온 새 판단도 이어 붙는다
  await expect(log.getByText('바뀐 경로가 MINOR라 기사 수락을 요청한다')).toBeVisible()
  // 스트림은 관리자 토큰과 지난 기록의 마지막 id로 붙고, 끊기면 받은 마지막 id로 다시 붙는다 (놓친 기록만 받는다)
  expect(streams[0]).toEqual({ auth: 'Bearer fixture-token', lastEventId: '7' })
  await expect.poll(() => streams[1]?.lastEventId).toBe('8')
  await expect(log.locator('.line .time').first()).toHaveText(/^\d{2}:\d{2}:\d{2}$/)
  await page.screenshot({ path: 'test-results/agent-log-all.png', fullPage: true })

  // 오류 결과는 붉게 표시되는 줄이다
  await expect(log.locator('.line.problem')).toHaveCount(1)

  // 줄 종류를 누르면 세부(JSON)가 펼쳐진다
  await log.getByRole('button', { name: '판단' }).first().click()
  await expect(log.locator('pre.detail')).toContainText('"rationale"')

  // 명령 줄: 판단만 보기 → 도구 호출 줄이 사라진다
  const prompt = page.getByLabel('agent-log$')
  await prompt.fill('kind decision')
  await prompt.press('Enter')
  await expect(log.getByText('→ business_request_route_plan(kind=REPLAN, truck_ids=["TR-03"])')).toBeHidden()
  await expect(log.getByText('보이는 종류: 판단')).toBeVisible()
  await prompt.fill('kind all')
  await prompt.press('Enter')

  // 작업을 고르면 그 작업 줄만 남는다
  await page.getByRole('complementary', { name: '로그에 나온 작업' }).getByText('qa').click()
  await expect(log.getByText('모델 경로 시작 · qa · 도구 8개 중에서 모델이 고른다')).toBeVisible()
  await expect(log.getByText('진입 불가 보고라 ST-1660을 빼고 TR-03만 다시 계산한다')).toBeHidden()
  await expect(page.getByText(`작업 ${OTHER}`, { exact: false })).toBeVisible()

  await page.screenshot({ path: 'test-results/agent-log.png', fullPage: true })
})

test('관리자 로그는 관리자만 연다: 기사·책임자는 첫 화면으로 돌아간다', async ({ page }) => {
  for (const [role, home] of [['DRIVER', '/driver'], ['EXECUTIVE', '/report']] as const) {
    await login(page, role)
    // 소스 파일(/src/shared/api/...)은 건드리지 않게 backend 주소만 막는다
    await page.route(/^http:\/\/127\.0\.0\.1:5185\/api\//, (route) => route.fulfill({ status: 404, json: { title: '미정의 API' } }))
    await page.goto('/admin/agent-log')
    await expect(page).toHaveURL(new RegExp(`${home}$`))
  }
})
