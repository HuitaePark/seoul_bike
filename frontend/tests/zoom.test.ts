import { test } from 'node:test'
import assert from 'node:assert/strict'
import { clamp, fit, MAX_ZOOM, panBy, wheelFactor, zoomAt, zoomOf } from '../src/features/dashboard/zoom.ts'

const home = fit({ minX: 0, minY: 0, maxX: 1000, maxY: 500 }, 0)

test('처음 보기는 내용 전체를 여백과 함께 담는다', () => {
  assert.deepEqual(fit({ minX: 10, minY: 20, maxX: 110, maxY: 70 }, 5), { x: 5, y: 15, w: 110, h: 60 })
  assert.equal(zoomOf(home, home), 1)
})

test('휠 확대는 커서 아래 지점을 화면에서 그대로 둔다', () => {
  const v = zoomAt(home, 2, 250, 100, home)
  assert.equal(zoomOf(v, home), 2)
  // 커서 지점의 보기 안 상대 위치가 확대 전과 같다
  assert.equal((250 - v.x) / v.w, (250 - home.x) / home.w)
  assert.equal((100 - v.y) / v.h, (100 - home.y) / home.h)
})

test('축소는 전체 보기까지, 확대는 MAX_ZOOM배까지만', () => {
  assert.equal(zoomAt(home, 0.5, 500, 250, home), home)
  const deep = zoomAt(home, 1000, 500, 250, home)
  assert.equal(zoomOf(deep, home), MAX_ZOOM)
  assert.equal(zoomAt(deep, 2, 500, 250, home), deep)
  // 확대한 데서 끝까지 줄이면 처음 보기 그 자체로 돌아온다
  assert.equal(zoomAt(zoomAt(home, 3, 100, 100, home), 0.01, 0, 0, home), home)
})

test('끌어 옮겨도 보기 가운데는 처음 보기 밖으로 나가지 않는다', () => {
  const v = zoomAt(home, 4, 500, 250, home)
  const moved = panBy(v, 100, -50, home)
  assert.equal(moved.x, v.x - 100)
  assert.equal(moved.y, v.y + 50)
  const far = panBy(v, -99999, -99999, home)
  assert.equal(far.x + far.w / 2, home.x + home.w)
  assert.equal(far.y + far.h / 2, home.y + home.h)
  assert.deepEqual(clamp(v, home), v)
})

test('휠 배수: 위로 굴리면 확대, 아래로 굴리면 축소, 줄·쪽 단위도 픽셀로 맞추고 한 번에 너무 크게 바뀌지 않는다', () => {
  assert.ok(wheelFactor(-100) > 1)
  assert.ok(wheelFactor(100) < 1)
  assert.equal(wheelFactor(3, 1), wheelFactor(48))
  assert.equal(wheelFactor(5000), wheelFactor(300))
  assert.equal(wheelFactor(0), 1)
})
