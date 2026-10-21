import { test } from 'node:test'
import assert from 'node:assert/strict'
import { legOf, previewMarks, previewOf, splitAtTruck } from '../src/features/dashboard/route-view.ts'
import { exactRegion, makeShiftId, searchRegions, todayInSeoul } from '../src/shared/region/regions.ts'
import type { Schemas } from '../src/shared/api/client'

type Station = Schemas['StationStatus']
type Route = Schemas['Route']

const st = (stationId: string, lat: number, lon: number, name = stationId): Station =>
  ({ stationId, name, lat, lon, docks: 20, bikes: 5, risk: 'NONE', observedAt: '2026-10-21T07:00:00+09:00' }) as Station
const stations = [st('A', 37.54, 127.04, '서울숲 정문'), st('B', 37.54, 127.06, '성수역 3번출구'), st('C', 37.55, 127.06, '뚝섬유원지역')]
const byId = new Map(stations.map((s) => [s.stationId, s]))
const route = (done: number): Route => ({
  truckId: 'truck-1',
  stops: ['A', 'B', 'C'].map((stationId, i) => ({ seq: i + 1, stationId, action: 'PICKUP', bikes: 1, status: i < done ? 'DONE' : 'PENDING' })),
}) as Route
const truck = (lat: number, lon: number) => ({ truckId: 'truck-1', driver: '', lat, lon, load: 0, capacity: 10, moving: true, updatedSec: 1 })

test('트럭 위치를 마친 정류장 → 다음 정류장 구간에 투영해 온 비율을 구한다', () => {
  const leg = legOf(route(1), truck(37.54, 127.05), byId)
  assert.equal(leg?.next, 1)
  assert.ok(Math.abs(leg!.ratio - 0.5) < 0.01)
  // 구간 밖이면 0~1로 자른다
  assert.equal(legOf(route(1), truck(37.54, 127.09), byId)?.ratio, 1)
  assert.equal(legOf(route(1), truck(37.54, 127.0), byId)?.ratio, 0)
})

test('첫 정류장 전이거나 다 마쳤으면 이을 구간이 없다', () => {
  assert.equal(legOf(route(0), truck(37.54, 127.05), byId), null)
  assert.equal(legOf(route(3), truck(37.54, 127.05), byId), null)
  assert.equal(legOf(route(1), undefined, byId), null)
})

const routeWith = (...status: string[]): Route => ({
  truckId: 'truck-1',
  stops: status.map((s, i) => ({ seq: i + 1, stationId: ['A', 'B', 'C'][i]!, action: 'PICKUP', bikes: 1, status: s })),
}) as Route

test('건너뛴(SKIPPED) 정류장도 지난 것으로 보고 다음 정류장은 첫 PENDING이다', () => {
  // DONE 개수(2)로 세면 건너뛴 B를 가리킨다
  assert.equal(legOf(routeWith('DONE', 'SKIPPED', 'PENDING'), truck(37.545, 127.06), byId)?.next, 2)
  assert.equal(legOf(routeWith('SKIPPED', 'PENDING', 'PENDING'), truck(37.54, 127.05), byId)?.next, 1)
})

test('건너뛴 정류장을 섞어 다 마쳤으면 이을 구간이 없다', () => {
  assert.equal(legOf(routeWith('DONE', 'SKIPPED', 'DONE'), truck(37.54, 127.05), byId), null)
  assert.equal(legOf(routeWith('DONE', 'DONE', 'SKIPPED'), truck(37.54, 127.05), byId), null)
  assert.equal(legOf(routeWith('SKIPPED', 'SKIPPED', 'SKIPPED'), truck(37.54, 127.05), byId), null)
})

test('경로 선은 트럭이 온 지점에서 지난 구간과 남은 구간으로 나뉜다', () => {
  const pts: [number, number][] = [[0, 0], [10, 0], [10, 10]]
  const split = splitAtTruck(pts, 1, { next: 1, ratio: 0.3 })
  assert.deepEqual(split.tip, [3, 0])
  assert.deepEqual(split.done, [[0, 0], [3, 0]])
  assert.deepEqual(split.todo, [[3, 0], [10, 0], [10, 10]])
  // 구간을 모르면 마친 정류장에서 끊는다 (예전 그림과 같다)
  assert.deepEqual(splitAtTruck(pts, 2, null), { done: [[0, 0], [10, 0]], todo: [[10, 0], [10, 10]], tip: null })
})

test('경로 변경 카드는 바뀐 순서와 빠지는·새로 들르는 대여소를 지도에 겹친다', () => {
  const card = {
    approvalId: 'x', kind: 'ROUTE_CHANGE', approver: 'MANAGER', summary: '경로 변경', truckId: 'TR-04',
    diff: { truckId: 'TR-04', before: [{ seq: 2, stationId: 'A', action: 'DROPOFF', bikes: 1 }, { seq: 3, stationId: 'B', action: 'DROPOFF', bikes: 1 }], after: [{ seq: 2, stationId: 'A', action: 'DROPOFF', bikes: 3 }, { seq: 3, stationId: 'C', action: 'PICKUP', bikes: 2 }] },
  } as Parameters<typeof previewOf>[0]
  const p = previewOf(card, stations)
  assert.equal(p.truckId, 'TR-04')
  assert.deepEqual(p.after, ['A', 'C'])
  assert.deepEqual(p.removed, ['B'])
  assert.deepEqual(p.added, ['C'])
  assert.deepEqual(previewMarks(p).map((m) => `${m.kind}:${m.id}`), ['removed:B', 'added:C'])
})

test('경로가 없는 카드(제약 추가)는 문장에 나온 대여소를 짚는다', () => {
  const card = { approvalId: 'y', kind: 'ADD_CONSTRAINT', approver: 'MANAGER', summary: '뚝섬유원지역 10:00~22:00 대여·반납 중지' } as Parameters<typeof previewOf>[0]
  const p = previewOf(card, stations)
  assert.equal(p.truckId, null)
  assert.deepEqual(p.stations, ['C'])
  assert.deepEqual(previewMarks(p), [{ id: 'C', kind: 'named' }])
})

test('근무조 ID는 권역·날짜·시간대를 합치고 날짜는 서울 기준이다', () => {
  assert.equal(makeShiftId(' S05 ', '2026-10-21', 'AM'), 'S05:2026-10-21:AM')
  // 서울 07:30(UTC 전날 22:30)에도 오늘 날짜다
  assert.equal(todayInSeoul(new Date('2026-10-20T22:30:00Z')), '2026-10-21')
  assert.deepEqual(searchRegions('강남-0').map((r) => r.id).slice(0, 2), ['S01', 'S02'])
  assert.deepEqual(searchRegions('광진').map((r) => r.id), ['N02'])
  assert.equal(searchRegions('').length, 17)
})

test('권역 칸은 ID나 이름과 정확히 같은 글자만 권역으로 받는다', () => {
  assert.equal(exactRegion(' s05 ')?.id, 'S05')
  assert.equal(exactRegion('강북-02')?.id, 'N02')
  assert.equal(exactRegion('강남-05')?.id, 'S05')
  // 고르다 만 검색어는 값이 되지 않는다 (예전에는 `광진:날짜:AM`이 나갔다)
  assert.equal(exactRegion('광진'), undefined)
  assert.equal(exactRegion(''), undefined)
})
