<!--
위험 거점 지도 (FR-01, FR-06). 실제 지도(MapLibre, 배경은 OpenFreeMap) 위에 대여소·트럭·경로를 겹친다.
대여소 색은 위험 종류, 크기는 거치대 수, 선은 트럭별 경로(지난 구간 실선, 남은 구간 점선, 트럭 위치에서 나뉜다)다.
트럭을 고르거나 승인 대기 변경안을 미리 보면(preview) 관련 없는 대여소·경로를 흐리게 한다.
배경지도는 외부에서 받는다. 못 받으면(발표장 인터넷 차단 등) 빈 바탕 위에 대여소·경로만 그린다.
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { GeoJSONSource, Map as MapLibreMap, Marker } from 'maplibre-gl'
import type * as MapLibreModule from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import type { Schemas } from '../../shared/api/client'
import { emptyStyle, fetchBasemap, koreanLabels } from '../../shared/map/basemap'
import type { RoutePreview, TruckLive } from './route-view'
import { legOf, markText, placed, previewMarks, splitAtTruck } from './route-view'

const props = defineProps<{
  stations: Schemas['StationStatus'][]
  routes: Schemas['Route'][]
  trucks: TruckLive[]
  colors: Record<string, string>
  selected?: string | null
  focusTruck?: string | null
  preview?: RoutePreview | null
  /** 이 위험 종류의 대여소만 그린다 (권역 상태 색 버튼). 지도 범위는 그대로다 */
  only?: Schemas['RiskKind'] | null
}>()
const emit = defineEmits<{ select: [stationId: string] }>()

type LngLat = [number, number]
const at = (s: { lat: number; lon: number }): LngLat => [s.lon, s.lat]
/** 대여소 원 반지름(px): 거치대 수에 따라 조금 커진다 */
const stationR = (docks: number) => 6 + Math.min(docks, 40) / 8

const byId = computed(() => new Map(props.stations.map((s) => [s.stationId, s])))

// 밝게 볼 트럭(변경안 미리보기 > 골라 본 트럭)과 밝게 둘 대여소. 고르지 않았으면 전부 밝다.
const focusing = computed(() => !!props.preview || !!props.focusTruck)
const focus = computed(() => (props.preview ? props.preview.truckId : props.focusTruck ?? null))
const lit = computed<Set<string> | null>(() => {
  if (!focusing.value) return null
  const route = props.routes.find((r) => r.truckId === focus.value)
  return new Set([...(route?.stops.map((s) => s.stationId) ?? []), ...(props.preview?.stations ?? []), ...(props.selected ? [props.selected] : [])])
})

const points = computed(() =>
  props.stations.filter((s) => !props.only || s.risk === props.only).map((s) => ({ ...s, r: stationR(s.docks), dim: !!lit.value && !lit.value.has(s.stationId) })),
)

// 트럭마다 지난 구간(첫 정류장~트럭 위치)과 남은 구간(트럭 위치~끝)을 나눠 그린다.
const lines = computed(() =>
  props.routes.map((r) => {
    const ss = r.stops.map((s) => byId.value.get(s.stationId)).filter((s) => !!s)
    const doneCount = r.stops.filter((s) => s.status === 'DONE').length
    const leg = ss.length === r.stops.length ? legOf(r, props.trucks.find((t) => t.truckId === r.truckId), byId.value) : null
    return { truckId: r.truckId, ...splitAtTruck(ss.map(at), doneCount, leg), dim: focusing.value && focus.value !== r.truckId }
  }),
)

// 위치를 아는(정류장을 하나라도 마친) 트럭만 놓는다
const trucks = computed(() => props.trucks.filter(placed).map((t) => ({ ...t, dim: focusing.value && focus.value !== t.truckId })))

// 변경안: 트럭이 지금 있는 곳 → 바뀐 정류장 순서, 빠지는 곳·새로 들르는 곳 표시
const previewLine = computed<LngLat[]>(() => {
  const p = props.preview
  if (!p?.truckId || !p.after.length) return []
  const t = trucks.value.find((x) => x.truckId === p.truckId)
  const start = lines.value.find((l) => l.truckId === p.truckId)?.tip ?? (t ? at(t) : null)
  const pts = p.after.map((id) => byId.value.get(id)).filter((s) => !!s).map(at)
  return start ? [start, ...pts] : pts
})
const marks = computed(() =>
  previewMarks(props.preview).flatMap((m) => {
    const s = byId.value.get(m.id)
    return s ? [{ ...m, at: at(s), r: stationR(s.docks) }] : []
  }),
)

type Line = { type: 'Feature'; properties: Record<string, string | number>; geometry: { type: 'LineString'; coordinates: LngLat[] } }
type Lines = { type: 'FeatureCollection'; features: Line[] }
const routeData = computed<Lines>(() => ({
  type: 'FeatureCollection',
  features: lines.value.flatMap((l) => {
    const op = l.dim ? 0.1 : props.preview ? 0.55 : 1
    const color = props.colors[l.truckId] ?? '#64748b'
    const line = (kind: 'done' | 'todo', coordinates: LngLat[]): Line[] =>
      coordinates.length > 1 ? [{ type: 'Feature', properties: { kind, color, op }, geometry: { type: 'LineString', coordinates } }] : []
    return [...line('done', l.done), ...line('todo', l.todo)]
  }),
}))
const previewData = computed<Lines>(() => ({
  type: 'FeatureCollection',
  features: previewLine.value.length > 1 ? [{ type: 'Feature', properties: {}, geometry: { type: 'LineString', coordinates: previewLine.value } }] : [],
}))

/** 대여소가 있는 범위. 재고만 바뀌면 같아서 지도를 다시 맞추지 않는다 (구역을 바꾸면 다시 맞춘다) */
const bounds = computed<[LngLat, LngLat] | null>(() => {
  if (!props.stations.length) return null
  const lons = props.stations.map((s) => s.lon)
  const lats = props.stations.map((s) => s.lat)
  return [[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]]
})

const container = ref<HTMLDivElement>()
const state = ref<'loading' | 'ready' | 'failed'>('loading')
const basemap = ref<'loading' | 'ready' | 'failed'>('loading')
let map: MapLibreMap | undefined
let lib: typeof MapLibreModule | undefined
let observer: ResizeObserver | undefined
let basemapCtl: AbortController | undefined
let styleChanging = false
let alive = false
const stationMarkers = new Map<string, { marker: Marker; el: HTMLButtonElement; dot: HTMLSpanElement; name: HTMLSpanElement }>()
const truckMarkers = new Map<string, { marker: Marker; el: HTMLDivElement; label: HTMLSpanElement }>()
let markMarkers: Marker[] = []

// 위쪽은 화면 전환·트럭 버튼이 덮고, 대여소 위에 트럭 이름표(약 40px)가 선다
const FIT = { padding: { top: 110, right: 40, bottom: 40, left: 40 }, maxZoom: 16 }
function fit() {
  const b = bounds.value
  if (!map || !b) return
  map.fitBounds(b, { ...FIT, duration: 0 })
}

function upsertLayers(m: MapLibreMap) {
  for (const [id, data] of [['routes', routeData.value], ['preview', previewData.value]] as const) {
    const source = m.getSource(id) as GeoJSONSource | undefined
    if (source) source.setData(data)
    else m.addSource(id, { type: 'geojson', data })
  }
  if (m.getLayer('route-done')) return
  const round = { 'line-cap': 'round', 'line-join': 'round' } as const
  m.addLayer({ id: 'route-done', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'done'], layout: round, paint: { 'line-color': ['get', 'color'], 'line-width': 5, 'line-opacity': ['*', 0.45, ['get', 'op']] } })
  m.addLayer({ id: 'route-todo', type: 'line', source: 'routes', filter: ['==', ['get', 'kind'], 'todo'], layout: round, paint: { 'line-color': ['get', 'color'], 'line-width': 4, 'line-opacity': ['get', 'op'], 'line-dasharray': [0.5, 2.25] } })
  m.addLayer({ id: 'preview-casing', type: 'line', source: 'preview', layout: round, paint: { 'line-color': '#fff', 'line-width': 9 } })
  m.addLayer({ id: 'preview-line', type: 'line', source: 'preview', layout: round, paint: { 'line-color': '#ea580c', 'line-width': 4.5, 'line-dasharray': [2.7, 1.3] } })
}

function syncStations(m: MapLibreMap, L: typeof MapLibreModule) {
  const keep = new Set<string>()
  for (const p of points.value) {
    keep.add(p.stationId)
    let entry = stationMarkers.get(p.stationId)
    if (!entry) {
      const el = document.createElement('button')
      el.type = 'button'
      const body = document.createElement('span')
      body.className = 'st'
      const pulse = document.createElement('span')
      pulse.className = 'st-pulse'
      const dot = document.createElement('span')
      dot.className = 'st-dot'
      const name = document.createElement('span')
      name.className = 'st-name'
      body.append(pulse, dot, name)
      el.append(body)
      const id = p.stationId
      el.addEventListener('click', (e) => { e.stopPropagation(); emit('select', id) })
      entry = { marker: new L.Marker({ element: el, anchor: 'center' }).setLngLat(at(p)).addTo(m), el, dot, name }
      stationMarkers.set(id, entry)
    }
    const selected = p.stationId === props.selected
    // MapLibre가 붙인 위치용 class(maplibregl-marker 등)를 지우지 않게 className을 통째로 바꾸지 않는다
    entry.el.classList.add('station')
    for (const risk of ['EMPTY', 'OVERFLOW', 'NONE']) entry.el.classList.toggle(risk, p.risk === risk)
    entry.el.classList.toggle('selected', selected)
    entry.el.classList.toggle('dim', p.dim)
    entry.el.style.setProperty('--r', `${p.r}px`)
    entry.el.style.zIndex = selected ? '3' : p.risk === 'NONE' ? '1' : '2'
    entry.el.title = `${p.name} · 재고 ${p.bikes}/${p.docks} · 예측 ${p.forecastBikes}`
    entry.el.setAttribute('aria-label', `${p.name} 재고 ${p.bikes}/${p.docks}`)
    entry.el.setAttribute('aria-pressed', String(selected))
    entry.dot.textContent = String(p.bikes)
    entry.name.textContent = p.name
    entry.marker.setLngLat(at(p))
  }
  for (const [id, e] of stationMarkers) if (!keep.has(id)) { e.marker.remove(); stationMarkers.delete(id) }
}

function syncTrucks(m: MapLibreMap, L: typeof MapLibreModule) {
  const keep = new Set<string>()
  for (const t of trucks.value) {
    keep.add(t.truckId)
    let entry = truckMarkers.get(t.truckId)
    if (!entry) {
      const el = document.createElement('div')
      el.className = 'truck-marker'
      const label = document.createElement('span')
      label.className = 'tm-label'
      const tip = document.createElement('span')
      tip.className = 'tm-tip'
      const dot = document.createElement('span')
      dot.className = 'tm-dot'
      el.append(label, tip, dot)
      // 흰 점(반지름 6px)의 가운데가 트럭 위치에 오게 아래로 6px 내린다
      entry = { marker: new L.Marker({ element: el, anchor: 'bottom', offset: [0, 6] }).setLngLat(at(t)).addTo(m), el, label }
      truckMarkers.set(t.truckId, entry)
    }
    entry.el.style.setProperty('--c', props.colors[t.truckId] ?? '#64748b')
    entry.el.classList.toggle('dim', t.dim)
    entry.label.textContent = `${t.truckId} ${t.load}/${t.capacity}`
    entry.marker.setLngLat(at(t))
  }
  for (const [id, e] of truckMarkers) if (!keep.has(id)) { e.marker.remove(); truckMarkers.delete(id) }
}

function syncMarks(m: MapLibreMap, L: typeof MapLibreModule) {
  for (const mk of markMarkers) mk.remove()
  markMarkers = marks.value.map((mk) => {
    const el = document.createElement('div')
    el.className = `pv-mark ${mk.kind}`
    el.textContent = markText[mk.kind]
    return new L.Marker({ element: el, anchor: 'bottom', offset: [0, -(mk.r + 6)] }).setLngLat(mk.at).addTo(m)
  })
}

function sync() {
  if (!map || !lib || state.value !== 'ready') return
  // 배경지도로 바꾸는 중에는 스타일이 비어 있어 선을 못 넣는다. 바뀐 뒤(style.load) 다시 넣는다
  if (!styleChanging) upsertLayers(map)
  syncStations(map, lib)
  syncTrucks(map, lib)
  syncMarks(map, lib)
}

async function loadBasemap() {
  if (!map) return
  basemapCtl?.abort()
  const ctl = new AbortController()
  basemapCtl = ctl
  const timer = window.setTimeout(() => ctl.abort(), 10_000)
  basemap.value = 'loading'
  try {
    const style = koreanLabels(await fetchBasemap(ctl.signal))
    if (basemapCtl !== ctl || !map) return
    styleChanging = true
    map.setStyle(style, { diff: false })
  } catch {
    if (basemapCtl === ctl) basemap.value = 'failed'
  } finally {
    clearTimeout(timer)
  }
}

function teardown() {
  basemapCtl?.abort()
  observer?.disconnect()
  for (const e of stationMarkers.values()) e.marker.remove()
  for (const e of truckMarkers.values()) e.marker.remove()
  for (const mk of markMarkers) mk.remove()
  stationMarkers.clear()
  truckMarkers.clear()
  markMarkers = []
  map?.remove()
  map = undefined
  styleChanging = false
}

async function start() {
  try {
    lib = await import('maplibre-gl')
    if (!alive || !container.value) return
    lib.setWorkerUrl(workerUrl)
    const b = bounds.value
    map = new lib.Map({
      container: container.value,
      style: structuredClone(emptyStyle),
      bounds: b ?? undefined,
      fitBoundsOptions: FIT,
      maxPitch: 0,
      attributionControl: { compact: true },
      locale: { 'NavigationControl.ZoomIn': '확대', 'NavigationControl.ZoomOut': '축소', 'AttributionControl.ToggleAttribution': '지도 출처' },
    })
    map.dragRotate.disable()
    map.touchZoomRotate.disableRotation()
    map.getCanvas().setAttribute('aria-label', '위험 거점 지도')
    // 위쪽은 화면 전환·트럭 버튼, 왼쪽 아래는 다음 도착 카드 자리라 지도 단추·축척은 오른쪽 아래에 둔다
    map.addControl(new lib.ScaleControl({ unit: 'metric' }), 'bottom-right')
    map.addControl(new lib.NavigationControl({ showCompass: false }), 'bottom-right')
    map.on('style.load', () => {
      if (styleChanging) {
        styleChanging = false
        basemap.value = 'ready'
      }
      state.value = 'ready'
      sync()
    })
    map.once('load', () => void loadBasemap())
    map.on('webglcontextlost', () => { state.value = 'failed' })
    observer = new ResizeObserver(() => map?.resize())
    observer.observe(container.value)
  } catch {
    // WebGL을 못 쓰는 브라우저
    state.value = 'failed'
  }
}

watch([routeData, previewData, points, trucks, marks, () => props.selected, () => props.colors], sync)
watch(() => JSON.stringify(bounds.value), fit)
onMounted(() => { alive = true; void start() })
onBeforeUnmount(() => { alive = false; teardown() })
</script>

<template>
  <div class="station-map" :data-map-state="state">
    <div ref="container" class="map-canvas" />
    <p v-if="state === 'failed'" class="map-note" role="alert">이 브라우저에서는 지도를 그릴 수 없다. 3D 관제 뷰를 쓴다.</p>
    <p v-else-if="basemap === 'failed'" class="map-note" role="status">
      배경지도를 받지 못해 대여소와 경로만 그렸다.
      <button type="button" @click="loadBasemap">다시 받기</button>
    </p>
  </div>
</template>

<!-- 대여소·트럭 표시는 MapLibre가 만든 DOM이라 scoped(data-v 속성)가 붙지 않는다. .station-map 아래로만 건다 -->
<style>
/* isolation: 마커의 z-index가 지도 안에서만 겨루게 한다. 없으면 대여소 마커가 지도 위에 뜬 화면 전환·트럭 버튼을 덮는다 */
.station-map { position: relative; flex: 1; min-height: 480px; isolation: isolate; }
.station-map .map-canvas { position: absolute; inset: 0; }
.station-map .map-note { position: absolute; left: 50%; bottom: 44px; transform: translateX(-50%); z-index: 2; margin: 0; padding: 8px 12px; border-radius: 10px; background: rgb(255 255 255 / 0.95); color: var(--ink-2); font-size: 13px; box-shadow: var(--shadow); }
.station-map .map-note button { margin-left: 6px; padding: 3px 10px; font-size: 12px; }
.station-map .maplibregl-ctrl-attrib { font-size: 10px; }

.station-map .station { width: calc(var(--r) * 2); height: calc(var(--r) * 2); padding: 0; border: 0; background: none; cursor: pointer; }
.station-map .station .st { position: absolute; inset: 0; transition: opacity 0.2s; }
.station-map .station.dim .st { opacity: 0.2; }
.station-map .st-dot { position: absolute; inset: 0; display: grid; place-items: center; border: 2.5px solid #fff; border-radius: 50%; color: #fff; font-size: 8.5px; font-weight: 700; line-height: 1; }
.station-map .st-pulse { position: absolute; inset: -4px; border-radius: 50%; opacity: 0.16; animation: st-pulse 2.4s ease-in-out infinite; }
.station-map .station.NONE .st-pulse { display: none; }
.station-map .station.EMPTY .st-dot, .station-map .station.EMPTY .st-pulse { background: var(--empty); }
.station-map .station.OVERFLOW .st-dot, .station-map .station.OVERFLOW .st-pulse { background: var(--overflow); }
.station-map .station.NONE .st-dot { background: var(--ok); }
.station-map .station.selected .st-dot { border: 3.5px solid var(--ink); }
/* 이름은 원 아래에 둔다. 위쪽은 그 대여소에 서 있는 트럭 이름표 자리다 */
.station-map .st-name { position: absolute; top: calc(100% + 4px); left: 50%; transform: translateX(-50%); white-space: nowrap; font-size: 11px; font-weight: 600; color: #334155; text-shadow: 0 0 3px #fff, 0 0 3px #fff, 0 0 3px #fff; opacity: 0; transition: opacity 0.15s; pointer-events: none; }
.station-map .station:hover .st-name, .station-map .station:focus-visible .st-name, .station-map .station.selected .st-name { opacity: 1; }
@keyframes st-pulse { 50% { transform: scale(1.35); opacity: 0.05; } }

.station-map .truck-marker { display: flex; flex-direction: column; align-items: center; pointer-events: none; filter: drop-shadow(0 2px 3px rgb(15 23 42 / 0.18)); transition: opacity 0.2s; }
.station-map .truck-marker.dim { opacity: 0.3; }
.station-map .tm-label { padding: 3px 9px; border-radius: 11px; background: var(--c); color: #fff; font-size: 11px; font-weight: 700; white-space: nowrap; }
.station-map .tm-tip { width: 0; height: 0; border: 6px solid transparent; border-top: 8px solid var(--c); border-bottom: 0; }
.station-map .tm-dot { width: 12px; height: 12px; margin-top: 2px; border: 3px solid var(--c); border-radius: 50%; background: #fff; box-sizing: border-box; }

.station-map .pv-mark { padding: 2px 8px; border-radius: 10px; color: #fff; font-size: 11px; font-weight: 800; white-space: nowrap; pointer-events: none; }
.station-map .pv-mark.removed { background: #dc2626; }
.station-map .pv-mark.added { background: #059669; }
.station-map .pv-mark.named { background: #ea580c; }
</style>
