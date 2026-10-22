// 3D 관제 뷰의 땅에 깔 배경지도 그림. 화면 밖에서 MapLibre로 구역을 북쪽이 위인 평면 지도로 한 번 그려 그림(JPEG)으로 뜬다.
// 3D 뷰는 위경도를 땅 격자에 곧게(선형으로) 놓고 아이소메트릭으로 기울이므로, 이 그림을 같은 행렬로 기울이면 기둥과 지도가 맞는다.
// 구역 하나(수 km)에서는 메르카토르와 위경도 선형의 차이가 수 m라 무시한다.
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import { fetchBasemap, koreanLabels } from '../../shared/map/basemap'

export type Bounds = { west: number; south: number; east: number; north: number }
/** 그림과 그림이 실제로 덮는 범위 (요청한 범위보다 한쪽이 조금 넓을 수 있다) */
export type GroundMap = Bounds & { url: string }

/** 그림 긴 변(px). 처음 보기(약 900px)의 2배라 두 배 확대까지 또렷하다. WebGL 캔버스 한계(보통 4096)보다 작게 둔다 */
const LONG_SIDE = 2048
const TIMEOUT_MS = 20_000

// 3D와 평면을 오가거나 다시 들어와도 같은 구역은 다시 그리지 않는다
const cache = new Map<string, Promise<GroundMap>>()

export function groundMap(b: Bounds): Promise<GroundMap> {
  const key = [b.west, b.south, b.east, b.north].map((n) => n.toFixed(5)).join(',')
  let hit = cache.get(key)
  if (!hit) {
    hit = render(b)
    // 실패(외부망 차단 등)는 기억하지 않는다. 다음에 다시 시도한다
    hit.catch(() => cache.delete(key))
    cache.set(key, hit)
  }
  return hit
}

async function render(b: Bounds): Promise<GroundMap> {
  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS)
  try {
    // 지도 엔진(약 1MB)은 3D 뷰가 땅 그림을 처음 그릴 때만 받는다 (평면 지도 PlanMap·StationMap과 같은 지연 로딩)
    const lib = await import('maplibre-gl')
    lib.setWorkerUrl(workerUrl)
    const style = koreanLabels(await fetchBasemap(controller.signal))

    // 그림 비율 = 실제 동서·남북 거리 비율 (경도 1도는 위도 cos만큼 짧다)
    const kx = Math.cos((((b.south + b.north) / 2) * Math.PI) / 180)
    const aspect = ((b.east - b.west) * kx) / (b.north - b.south)
    const [w, h] = aspect >= 1 ? [LONG_SIDE, Math.round(LONG_SIDE / aspect)] : [Math.round(LONG_SIDE * aspect), LONG_SIDE]

    // 크기가 있어야 그린다. 화면 밖에 둔다
    const box = document.createElement('div')
    box.setAttribute('aria-hidden', 'true')
    Object.assign(box.style, { position: 'fixed', left: '-100000px', top: '0', width: `${w}px`, height: `${h}px`, pointerEvents: 'none' })
    document.body.append(box)
    const map = new lib.Map({
      container: box,
      style,
      bounds: [[b.west, b.south], [b.east, b.north]],
      fitBoundsOptions: { padding: 0 },
      interactive: false,
      attributionControl: false,
      // 다 그린 캔버스를 그림으로 뜨려면 버퍼를 남겨야 한다
      canvasContextAttributes: { preserveDrawingBuffer: true },
      pixelRatio: 1,
      fadeDuration: 0,
    })
    try {
      // 앱 tsconfig의 lib가 ES2024 전이라 Promise.withResolvers 대신 실행자 꼴을 쓴다
      await new Promise<void>((resolve, reject) => {
        controller.signal.addEventListener('abort', () => reject(new Error('배경지도 시간 초과')))
        map.once('idle', () => resolve())
      })
      const got = map.getBounds()
      return { url: map.getCanvas().toDataURL('image/jpeg', 0.86), west: got.getWest(), south: got.getSouth(), east: got.getEast(), north: got.getNorth() }
    } finally {
      map.remove()
      box.remove()
    }
  } finally {
    clearTimeout(timer)
  }
}
