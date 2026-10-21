// 더미 데이터. backend API(/api/regions/{regionId}/stations, /api/shifts/{shiftId}/plan, /api/drivers/me/route)가
// 붙기 전까지 화면 모양을 잡는 용도다. 타입은 계약(openapi)과 같게 둬서 나중에 api 호출로 바꾸기만 하면 된다.
// 관제 대시보드와 기사 앱이 같은 근무조를 보도록 한 곳에 둔다.

import type { Schemas } from './api/client'

type StationStatus = Schemas['StationStatus']
type Plan = Schemas['Plan']

export const DUMMY_REGION = { id: 'gwangjin-seongdong', name: '광진·성동' }
const today = new Date().toISOString().slice(0, 10)
export const DUMMY_SHIFT_ID = `${DUMMY_REGION.id}:${today}:AM`

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

export const DUMMY_STATIONS: StationStatus[] = [
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

export const DUMMY_PLAN: Plan = {
  planId: 'plan-am-v3',
  shiftId: DUMMY_SHIFT_ID,
  version: 3,
  status: 'ACTIVE',
  explanation: '출근 시간 성수·건대 역세권 빈 대여소 5곳을 먼저 채우고, 서울숲·어린이대공원 과적재분으로 충당한다.',
  routes: [
    {
      truckId: 'truck-1',
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
      truckId: 'truck-2',
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
      truckId: 'truck-3',
      capacity: 12,
      startLoad: 0,
      stops: [
        { seq: 1, stationId: 'ST-530', stationName: '한양대역 2번출구', action: 'PICKUP', bikes: 8, loadAfter: 8, eta: at(7, 15), status: 'DONE', reason: '22대 예상, 거치대 20 초과' },
        { seq: 2, stationId: 'ST-520', stationName: '왕십리역 6번출구', action: 'DROPOFF', bikes: 8, loadAfter: 0, eta: at(7, 28), status: 'DONE', reason: '07:45 빈 대여소 예상, 고도 28m' },
      ],
    },
  ],
  unserved: [
    { stationId: 'ST-563', stationName: '강변역 4번출구', priority: 0.41, reason: '트럭 적재량 부족 (truck-2 만차)' },
    { stationId: 'ST-555', stationName: '군자역 5번출구', priority: 0.36, reason: '근무조 시간 한계 (도착 08:55 > 08:30)' },
  ],
}

/** 트럭 실시간 상태 (FR-06). 계약에 아직 없다. */
export type TruckLive = {
  truckId: string
  driver: string
  lat: number
  lon: number
  load: number
  capacity: number
  moving: boolean
  updatedSec: number
}

export const DUMMY_TRUCKS: TruckLive[] = [
  { truckId: 'truck-1', driver: '김기사', lat: 37.5458, lon: 127.0518, load: 7, capacity: 15, moving: true, updatedSec: 3 },
  { truckId: 'truck-2', driver: '이기사', lat: 37.5440, lon: 127.0720, load: 12, capacity: 15, moving: true, updatedSec: 2 },
  { truckId: 'truck-3', driver: '박기사', lat: 37.5612, lon: 127.0371, load: 0, capacity: 12, moving: false, updatedSec: 4 },
]

export const DUMMY_PENDING: (Schemas['ApprovalRequest'] & { createdMin: number })[] = [
  {
    approvalId: '00000000-0000-4000-8000-000000000101',
    kind: 'ROUTE_CHANGE',
    approver: 'MANAGER',
    grade: 'MAJOR',
    requiredApprovers: ['MANAGER', 'DRIVER'],
    truckId: 'truck-2',
    summary: '구의역 보충분을 truck-1로 넘기고 truck-2는 강변역 회수',
    reasons: ['건대입구역 실제 재고 0대 (앱 4대) 보고', 'truck-2 적재량 부족, 트럭 간 배정 이동이라 큰 변경'],
    createdMin: 2,
    diff: {
      truckId: 'truck-2',
      addedMinutes: 6,
      before: DUMMY_PLAN.routes[1]!.stops.slice(1),
      after: [
        { seq: 2, stationId: 'ST-541', action: 'DROPOFF', bikes: 12 },
        { seq: 3, stationId: 'ST-570', action: 'PICKUP', bikes: 5 },
        { seq: 4, stationId: 'ST-563', action: 'PICKUP', bikes: 4 },
      ],
    },
  },
  {
    approvalId: '00000000-0000-4000-8000-000000000102',
    kind: 'ADD_CONSTRAINT',
    approver: 'MANAGER',
    summary: '뚝섬유원지역 10:00~22:00 대여·반납 중지 (한강 행사)',
    reasons: ['관리자 요청 "뚝섬 한강공원 행사로 대여소 운영 중지"를 제약으로 해석'],
    createdMin: 9,
  },
]
