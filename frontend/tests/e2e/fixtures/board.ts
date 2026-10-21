// 관제 대시보드·기사 앱 e2e 고정 자료. 예전 화면 더미(src/shared/dummy.ts)와 기사 앱의 변경 제안 더미를 옮겼다:
// 화면은 이제 실제 API를 읽으므로 배치·상호작용 시험은 page.route로 이 자료를 돌려준다. 대여소 ID·이름은 가상이다 (실제 N02 대여소가 아니다).

import type { Page } from '@playwright/test'
import type { Schemas } from '../../../src/shared/api/client'

type StationStatus = Schemas['StationStatus']
type Plan = Schemas['Plan']
type Task = Schemas['Task']

const observedAt = new Date().toISOString()
function st(
  stationId: string,
  name: string,
  lat: number,
  lon: number,
  docks: number,
  bikes: number,
  forecastBikes: number,
  risk: StationStatus['risk'],
  role: StationStatus['role'],
  elevationM: number,
): StationStatus {
  return { stationId, name, lat, lon, docks, bikes, forecastBikes, risk, role, elevationM, observedAt }
}

export const STATIONS: StationStatus[] = [
  st('ST-502', '뚝섬역 1번출구', 37.5473, 127.0473, 20, 2, 0, 'EMPTY', 'FIXED_DROPOFF', 18),
  st('ST-503', '성수역 3번출구', 37.5446, 127.0557, 25, 1, 0, 'EMPTY', 'FIXED_DROPOFF', 16),
  st('ST-511', '서울숲 정문', 37.5444, 127.0374, 30, 28, 34, 'OVERFLOW', 'FIXED_PICKUP', 12),
  st('ST-514', '서울숲역 4번출구', 37.5435, 127.0446, 15, 7, 6, 'NONE', 'VARIABLE', 14),
  st('ST-520', '왕십리역 6번출구', 37.5612, 127.0371, 20, 3, 1, 'EMPTY', 'VARIABLE', 28),
  st('ST-523', '행당역 2번출구', 37.5573, 127.0294, 15, 9, 8, 'NONE', 'VARIABLE', 35),
  st('ST-530', '한양대역 2번출구', 37.5554, 127.0436, 20, 17, 22, 'OVERFLOW', 'FIXED_PICKUP', 24),
  st('ST-541', '건대입구역 2번출구', 37.5404, 127.0692, 30, 4, 0, 'EMPTY', 'FIXED_DROPOFF', 20),
  st('ST-544', '건국대 정문', 37.5428, 127.0758, 20, 11, 10, 'NONE', 'VARIABLE', 26),
  st('ST-552', '어린이대공원역', 37.5480, 127.0745, 25, 22, 29, 'OVERFLOW', 'FIXED_PICKUP', 31),
  st('ST-555', '군자역 5번출구', 37.5572, 127.0795, 20, 8, 6, 'NONE', 'VARIABLE', 33),
  st('ST-561', '구의역 1번출구', 37.5371, 127.0857, 20, 2, 0, 'EMPTY', 'FIXED_DROPOFF', 22),
  st('ST-563', '강변역 4번출구', 37.5352, 127.0947, 25, 13, 12, 'NONE', 'VARIABLE', 19),
  st('ST-570', '자양사거리', 37.5340, 127.0731, 15, 14, 18, 'OVERFLOW', 'VARIABLE', 15),
  st('ST-575', '뚝섬유원지역', 37.5316, 127.0667, 30, 26, 31, 'OVERFLOW', 'FIXED_PICKUP', 11),
  st('ST-581', '성수동 카페거리', 37.5426, 127.0601, 15, 3, 1, 'EMPTY', 'VARIABLE', 17),
  st('ST-584', '성수 IT밸리', 37.5469, 127.0638, 20, 10, 9, 'NONE', 'VARIABLE', 19),
  st('ST-590', '응봉역 1번출구', 37.5500, 127.0349, 15, 6, 5, 'NONE', 'VARIABLE', 40),
]

const at = (h: number, m: number) => {
  const d = new Date()
  d.setHours(h, m, 0, 0)
  return d.toISOString()
}

const PLAN: Plan = {
  planId: 'plan-am-v3',
  shiftId: '',
  version: 3,
  status: 'ACTIVE',
  explanation: '출근 시간 성수·건대 역세권 빈 대여소 5곳을 먼저 채우고, 서울숲·어린이대공원 과적재분으로 충당한다.',
  routes: [
    {
      truckId: 'TR-03',
      capacity: 15,
      startLoad: 4,
      stops: [
        { seq: 1, stationId: 'ST-511', stationName: '서울숲 정문', action: 'PICKUP', bikes: 9, loadAfter: 13, eta: at(7, 10), status: 'DONE', reason: '3시간 뒤 34대 예상, 거치대 30 초과' },
        { seq: 2, stationId: 'ST-502', stationName: '뚝섬역 1번출구', action: 'DROPOFF', bikes: 6, loadAfter: 7, eta: at(7, 22), status: 'DONE', reason: '07:40 빈 대여소 예상, 고정 배치 거점' },
        { seq: 3, stationId: 'ST-503', stationName: '성수역 3번출구', action: 'DROPOFF', bikes: 7, loadAfter: 0, eta: at(7, 34), status: 'PENDING', reason: '현재 1대, 07:35 빈 대여소 예상' },
        { seq: 4, stationId: 'ST-575', stationName: '뚝섬유원지역', action: 'PICKUP', bikes: 10, loadAfter: 10, eta: at(7, 52), status: 'PENDING', reason: '한강공원 반납 몰림, 31대 예상' },
        { seq: 5, stationId: 'ST-581', stationName: '성수동 카페거리', action: 'DROPOFF', bikes: 6, loadAfter: 4, eta: at(8, 6), status: 'PENDING', reason: '08:20 빈 대여소 예상' },
        { seq: 6, stationId: 'ST-584', stationName: '성수 IT밸리', action: 'DROPOFF', bikes: 4, loadAfter: 0, eta: at(8, 15), status: 'PENDING', reason: '출근 대여 수요 상위 10%' },
      ],
    },
    {
      truckId: 'TR-04',
      capacity: 15,
      startLoad: 2,
      stops: [
        { seq: 1, stationId: 'ST-552', stationName: '어린이대공원역', action: 'PICKUP', bikes: 10, loadAfter: 12, eta: at(7, 12), status: 'DONE', reason: '29대 예상, 거치대 25 초과' },
        { seq: 2, stationId: 'ST-541', stationName: '건대입구역 2번출구', action: 'DROPOFF', bikes: 8, loadAfter: 4, eta: at(7, 25), status: 'PENDING', reason: '현재 4대, 07:30 빈 대여소 예상' },
        { seq: 3, stationId: 'ST-570', stationName: '자양사거리', action: 'PICKUP', bikes: 5, loadAfter: 9, eta: at(7, 38), status: 'PENDING', reason: '18대 예상, 거치대 15 초과' },
        { seq: 4, stationId: 'ST-561', stationName: '구의역 1번출구', action: 'DROPOFF', bikes: 9, loadAfter: 0, eta: at(7, 51), status: 'PENDING', reason: '현재 2대, 고정 배치 거점' },
      ],
    },
    {
      truckId: 'TR-05',
      capacity: 15,
      startLoad: 0,
      stops: [
        { seq: 1, stationId: 'ST-530', stationName: '한양대역 2번출구', action: 'PICKUP', bikes: 8, loadAfter: 8, eta: at(7, 15), status: 'DONE', reason: '22대 예상, 거치대 20 초과' },
        { seq: 2, stationId: 'ST-520', stationName: '왕십리역 6번출구', action: 'DROPOFF', bikes: 8, loadAfter: 0, eta: at(7, 28), status: 'DONE', reason: '07:45 빈 대여소 예상, 고도 28m' },
      ],
    },
  ],
  unserved: [
    { stationId: 'ST-563', stationName: '강변역 4번출구', priority: 0.41, reason: 'NOT_SELECTED' },
    { stationId: 'ST-555', stationName: '군자역 5번출구', priority: 0.36, reason: 'NOT_SELECTED' },
  ],
}

/** 요청한 근무조의 계획 (화면은 다른 근무조 계획을 버린다) */
export const planFor = (shiftId: string): Plan => ({ ...structuredClone(PLAN), shiftId })

const shiftOf = (url: string) => decodeURIComponent(new URL(url).pathname.split('/')[3]!)
const DRIVER_SHIFT = 'N02:2026-10-21:AM'

/** 재경로 작업이 기다리는 TR-04 기사 카드 (작은 변경, 기사 수락 한 장) */
const DRIVER_TASK: Task = {
  taskId: '00000000-0000-4000-8000-000000000301',
  type: 'FIELD_REPORT',
  status: 'WAITING_APPROVAL',
  shiftId: DRIVER_SHIFT,
  createdBy: 'fixture-driver2',
  createdAt: observedAt,
  updatedAt: observedAt,
  pendingApproval: {
    approvalId: '00000000-0000-4000-8000-000000000201',
    kind: 'ROUTE_CHANGE',
    approver: 'DRIVER',
    grade: 'MINOR',
    requiredApprovers: ['DRIVER'],
    truckId: 'TR-04',
    summary: '구의역 1번출구 보충을 2대 늘리고 자양사거리 회수는 2대 줄임',
    reasons: ['구의역 1번출구 실제 재고 0대 보고 (앱 2대)', '소요 시간 +2분, 같은 트럭 안의 변경이라 작은 변경'],
    diff: {
      truckId: 'TR-04',
      addedMinutes: 2,
      before: [
        { seq: 3, stationId: 'ST-570', action: 'PICKUP', bikes: 5 },
        { seq: 4, stationId: 'ST-561', action: 'DROPOFF', bikes: 9 },
      ],
      after: [
        { seq: 3, stationId: 'ST-570', action: 'PICKUP', bikes: 3 },
        { seq: 4, stationId: 'ST-561', action: 'DROPOFF', bikes: 11 },
      ],
    },
  },
}

/**
 * 관제 대시보드: GET /api/regions/{regionId}/stations, GET /api/shifts/{shiftId}/plan,
 * GET /api/shifts/{shiftId}/safety(무승인 0건)를 고정 자료로 답한다.
 */
export async function mockBoard(page: Page) {
  await page.route('**/api/regions/*/stations?**', (route) => route.fulfill({ json: STATIONS }))
  await page.route('**/api/shifts/*/plan', (route) => route.fulfill({ json: planFor(shiftOf(route.request().url())) }))
  await page.route('**/api/shifts/*/safety', (route) =>
    route.fulfill({ json: { shiftId: shiftOf(route.request().url()), approvedChanges: 3, unapprovedChanges: 0 } }))
}

/** 기사 앱(TR-04): 오늘 경로(DriverRoute), 구역 대여소, 승인 대기 작업(기사 카드 한 장), 보고·결정을 답한다. 보낸 몸통을 돌려준다. */
export async function mockDriver(page: Page) {
  const sent: { path: string; body: unknown }[] = []
  await page.route('**/api/drivers/me/route', (route) =>
    route.fulfill({ json: { ...PLAN.routes.find((r) => r.truckId === 'TR-04'), shiftId: DRIVER_SHIFT } }))
  await page.route('**/api/regions/*/stations?**', (route) => route.fulfill({ json: STATIONS }))
  await page.route('**/api/ai/tasks?**', (route) =>
    route.fulfill({ json: { items: sent.some((s) => s.path.endsWith('/approval')) ? [] : [DRIVER_TASK], page: 0, size: 50, total: 1 } }))
  await page.route('**/api/reports', (route) => {
    sent.push({ path: '/api/reports', body: route.request().postDataJSON() })
    return route.fulfill({ status: 202, json: { reportId: 'r-1', taskId: null } })
  })
  await page.route('**/api/ai/tasks/*/approval', (route) => {
    sent.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() })
    return route.fulfill({ status: 202, json: {} })
  })
  return sent
}
