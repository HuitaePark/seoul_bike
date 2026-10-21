<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { GeoJSONSource, Map as MapLibreMap, Marker } from 'maplibre-gl'
import type * as MapLibreModule from 'maplibre-gl'
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url'
import 'maplibre-gl/dist/maplibre-gl.css'
import { emptyStyle, fetchBasemap } from '../../shared/map/basemap.ts'
import { mapData, position, truckColor, type MapPoint, type Position } from './map-model.ts'
import { label, numberText, riskLabels, roleLabels, stationName, type Plan, type Station } from './model.ts'

const props = defineProps<{
  stations: Station[]; visibleStations: Station[]; plan: Plan | null
  selectedTruck: string; selectedStation: string; excluded: string[]
}>()
const emit = defineEmits<{ selectTruck: [id: string]; selectStation: [id: string] }>()
const searchText = defineModel<string>('searchText', { required: true })
const risk = defineModel<string>('risk', { required: true })
const stationRole = defineModel<string>('stationRole', { required: true })
const container = ref<HTMLDivElement>()
const ready = ref(false)
const fatal = ref(false)
const baseState = ref<'loading' | 'ready' | 'failed'>('loading')
const data = computed(() => mapData(props.stations, props.plan, props.selectedTruck, props.excluded))
const visibleIds = computed(() => new Set(props.visibleStations.map(s => s.stationId)))
const selected = computed(() => props.stations.find(s => s.stationId === props.selectedStation))
const selectedStops = computed(() => [...(props.plan?.routes.find(r => r.truckId === props.selectedTruck)?.stops ?? [])].sort((a, b) => a.seq - b.seq))
const stationsById = computed(() => new Map(props.stations.map(s => [s.stationId, s])))
const color = computed(() => truckColor(props.plan, props.selectedTruck))
const selectedMissing = computed(() => props.selectedStation && !position(selected.value))
let map: MapLibreMap | undefined
let library: typeof MapLibreModule | undefined
let observer: ResizeObserver | undefined
let styleController: AbortController | undefined
let styleTimer: number | undefined
let epoch = 0
let mounted = false
let styleChanging = false
const markers = new Map<string, { marker: Marker; button: HTMLButtonElement; dot: HTMLSpanElement }>()

function fit(points: Position[]) {
  if (!map || !points.length || fatal.value) return
  if (points.length === 1) { map.jumpTo({ center: points[0]!, zoom: 15 }); return }
  const xs = points.map(p => p[0]), ys = points.map(p => p[1])
  map.fitBounds([[Math.min(...xs), Math.min(...ys)], [Math.max(...xs), Math.max(...ys)]], { padding: 60, maxZoom: 15.5, duration: 0 })
}
function fitTruck() {
  fit(data.value.selectedPositions.length ? data.value.selectedPositions : data.value.points.map(p => p.position))
}
function fitAll() {
  fit(data.value.points.map(p => p.position))
}
function focusStation() {
  const p = position(selected.value)
  if (map && p && !fatal.value) map.easeTo({ center: p, zoom: Math.max(14, map.getZoom()), duration: 0 })
}
function describe(point: MapPoint) {
  const visits = point.visits.map(s => `${s.seq}번 ${s.action === 'PICKUP' ? '회수' : '배치'} ${s.bikes}대`).join(', ')
  return [point.station.name, label(riskLabels, point.station.risk), visits, point.unserved ? '미처리' : '', point.excluded ? '제외 요청 선택' : ''].filter(Boolean).join(' · ')
}
function syncOverlays() {
  if (!map || !library || !ready.value || fatal.value) return
  if (!styleChanging) {
    if (!map.getSource('visit-order')) map.addSource('visit-order', { type: 'geojson', data: data.value.lines })
    else (map.getSource('visit-order') as GeoJSONSource).setData(data.value.lines)
    if (!map.getLayer('visit-order-lines')) map.addLayer({
      id: 'visit-order-lines', type: 'line', source: 'visit-order',
      layout: { 'line-cap': 'round', 'line-join': 'round' },
      paint: {
        'line-color': ['get', 'color'], 'line-width': ['case', ['get', 'selected'], 4, 2],
        'line-opacity': ['case', ['get', 'selected'], 0.95, 0.22], 'line-dasharray': [2, 2],
      },
    })
  }
  const keep = new Set<string>()
  for (const point of data.value.points) {
    const id = point.station.stationId
    if (!visibleIds.value.has(id)) continue
    keep.add(id)
    let entry = markers.get(id)
    if (!entry) {
      const button = document.createElement('button')
      button.type = 'button'
      const dot = document.createElement('span')
      dot.className = 'map-marker-dot'; dot.setAttribute('aria-hidden', 'true'); button.append(dot)
      button.addEventListener('click', event => { event.stopPropagation(); emit('selectStation', id) })
      const marker = new library.Marker({ element: button, anchor: 'center' }).setLngLat(point.position).addTo(map)
      entry = { marker, button, dot }; markers.set(id, entry)
    }
    entry.marker.setLngLat(point.position)
    const active = props.selectedStation === id
    // Preserve MapLibre's positioning/anchor classes on this same button.
    entry.button.classList.add('map-station-marker')
    for (const value of ['EMPTY', 'OVERFLOW', 'NONE']) entry.button.classList.toggle(value, point.station.risk === value)
    for (const [value, enabled] of Object.entries({ 'on-route': !!point.visits.length, 'is-unserved': point.unserved, 'is-excluded': point.excluded, 'is-selected': active })) entry.button.classList.toggle(value, enabled)
    entry.button.style.setProperty('--truck-color', color.value)
    entry.button.setAttribute('aria-label', `지도에서 ${point.station.name} 선택`)
    entry.button.setAttribute('aria-pressed', String(active))
    entry.button.title = describe(point)
    entry.dot.textContent = point.visits.length ? point.visits.map(s => s.seq).join('·') : point.unserved ? '!' : ''
  }
  for (const [id, entry] of markers) if (!keep.has(id)) { entry.marker.remove(); markers.delete(id) }
}
function teardown() {
  epoch++; styleController?.abort(); clearTimeout(styleTimer); observer?.disconnect()
  for (const entry of markers.values()) entry.marker.remove()
  markers.clear(); map?.remove(); map = undefined; ready.value = false; styleChanging = false
}
async function loadBasemap() {
  if (!map || fatal.value) return
  styleController?.abort(); clearTimeout(styleTimer)
  const controller = new AbortController(); styleController = controller
  const currentMap = map
  baseState.value = 'loading'
  styleTimer = window.setTimeout(() => {
    controller.abort()
    if (styleController === controller && map === currentMap) {
      baseState.value = 'failed'
      if (styleChanging) { styleChanging = false; currentMap.setStyle(structuredClone(emptyStyle), { diff: false }) }
    }
  }, 10_000)
  try {
    const style = await fetchBasemap(controller.signal)
    if (styleController !== controller || map !== currentMap || controller.signal.aborted) return
    styleChanging = true
    currentMap.setStyle(style, { diff: false })
  } catch {
    if (styleController === controller && map === currentMap) {
      baseState.value = 'failed'; clearTimeout(styleTimer)
      if (styleChanging) { styleChanging = false; currentMap.setStyle(structuredClone(emptyStyle), { diff: false }) }
    }
  }
}
async function start() {
  teardown(); fatal.value = false; baseState.value = 'loading'
  if (!container.value || !data.value.points.length) return
  const current = epoch
  try {
    library = await import('maplibre-gl')
    if (!mounted || current !== epoch || !container.value) return
    library.setWorkerUrl(workerUrl)
    map = new library.Map({
      container: container.value, style: structuredClone(emptyStyle), center: data.value.points[0]!.position, zoom: 13,
      maxPitch: 0, attributionControl: { compact: true }, cooperativeGestures: true,
      locale: { 'NavigationControl.ZoomIn': '확대', 'NavigationControl.ZoomOut': '축소', 'AttributionControl.ToggleAttribution': '지도 출처' },
    })
    map.dragRotate.disable(); map.touchZoomRotate.disableRotation()
    map.getCanvas().setAttribute('aria-label', '대여소와 방문 순서 지도')
    map.addControl(new library.NavigationControl({ showCompass: false }), 'top-right')
    map.addControl(new library.ScaleControl({ unit: 'metric' }))
    map.on('style.load', () => {
      // Finish initial framing before enabling controls so load cannot undo a user's view.
      if (!ready.value) fitTruck()
      ready.value = true
      if (styleChanging) {
        styleChanging = false; clearTimeout(styleTimer)
        if (baseState.value === 'loading') baseState.value = 'ready'
      }
      syncOverlays()
    })
    map.once('load', () => { void loadBasemap() })
    map.on('error', () => { baseState.value = 'failed' })
    map.on('webglcontextlost', () => { fatal.value = true })
    observer = new ResizeObserver(() => map?.resize())
    observer.observe(container.value)
  } catch { if (current === epoch) { fatal.value = true; baseState.value = 'failed' } }
}
watch([data, visibleIds], syncOverlays)
watch(() => [props.selectedTruck, props.selectedStation], ([truck], [oldTruck]) => {
  syncOverlays()
  if (truck !== oldTruck) fitTruck()
  else focusStation()
})
watch(() => !!data.value.points.length, () => { if (mounted) void start() })
onMounted(() => { mounted = true; void start() })
onBeforeUnmount(() => { mounted = false; teardown() })
</script>

<template>
  <section class="panel plan-map-panel" aria-labelledby="map-heading" :data-map-state="fatal ? 'failed' : ready ? 'ready' : 'loading'">
    <div class="section-heading">
      <div><p class="eyebrow">권역 지도</p><h2 id="map-heading">대여소와 방문 순서</h2></div>
      <div class="map-view-buttons">
        <button class="ghost" :disabled="!ready || fatal || !data.points.length" @click="fitAll">전체 거점 보기</button>
        <button class="ghost" :disabled="!ready || fatal || !data.selectedPositions.length" @click="fitTruck">선택 트럭 보기</button>
      </div>
    </div>
    <div class="station-filters">
      <label>이름·ID 검색<input v-model="searchText" type="search" placeholder="대여소 검색"></label>
      <label>위험 분류<select v-model="risk" aria-label="위험 분류"><option value="ALL">전체 위험</option><option v-for="(text, key) in riskLabels" :key="key" :value="key">{{ text }}</option></select></label>
      <label>역할 분류<select v-model="stationRole" aria-label="역할 분류"><option value="ALL">전체 역할</option><option v-for="(text, key) in roleLabels" :key="key" :value="key">{{ text }}</option></select></label>
    </div>
    <div class="map-toolbar">
      <label v-if="plan?.routes.length">지도 트럭 선택<select :value="selectedTruck" aria-label="지도 트럭 선택" @change="emit('selectTruck', ($event.target as HTMLSelectElement).value)"><option v-for="r in plan.routes" :key="r.truckId" :value="r.truckId">{{ r.truckId }} · {{ r.stops.length }}곳</option></select></label>
      <div class="map-legend" aria-label="지도 범례"><span class="legend-empty">빈 위험</span><span class="legend-overflow">과적재 위험</span><span class="legend-unserved">! 미처리</span><span class="legend-order" :style="{ '--truck-color': color }">번호: 선택 트럭 순서</span><span>× 제외 요청 선택</span></div>
    </div>
    <div class="map-canvas-wrap">
      <div ref="container" class="map-canvas"></div>
      <p v-if="!data.points.length" class="map-overlay" role="status">표시할 대여소 좌표가 없습니다. 아래 목록에서 계획을 확인하세요.</p>
      <p v-else-if="fatal" class="map-overlay" role="alert">이 환경에서 지도를 표시할 수 없습니다. 아래 경로표와 상세 정보를 이용하세요.<button class="ghost" @click="start">지도 다시 열기</button></p>
      <p v-else-if="!ready" class="map-overlay" role="status">지도를 준비하고 있습니다.</p>
      <p v-else-if="baseState === 'loading'" class="map-loading" role="status">배경지도 불러오는 중…</p>
    </div>
    <p v-if="!fatal && baseState === 'failed' && data.points.length" class="callout warning" role="status">배경지도 일부를 불러오지 못했습니다. 대여소와 방문 순서는 계속 확인할 수 있습니다. <button class="ghost" @click="loadBasemap">배경지도 재시도</button></p>
    <p class="map-order-note"><strong>점선은 방문 순서 연결선입니다.</strong> 실제 도로 경로나 트럭 현재 위치를 나타내지 않습니다. 검색·필터는 마커와 아래 현황 표에 적용됩니다.</p>
    <div v-if="selectedStops.length" class="map-visit-list" aria-label="선택 트럭 방문 순서">
      <template v-for="(stop, index) in selectedStops" :key="stop.seq"><span v-if="index" aria-hidden="true">→</span><button class="ghost" :class="{ active: selectedStation === stop.stationId }" :aria-label="`${selectedTruck} ${stop.seq}번 ${stationName(stop.stationId, stationsById, stop.stationName)} 선택`" :aria-pressed="selectedStation === stop.stationId" @click="emit('selectStation', stop.stationId)"><span class="visit-number" :style="{ background: color }">{{ stop.seq }}</span>{{ stationName(stop.stationId, stationsById, stop.stationName) }} · {{ stop.action === 'PICKUP' ? '회수' : '배치' }} {{ stop.bikes }}대</button></template>
    </div>
    <p v-else-if="plan" class="muted">선택 트럭에 배정된 방문이 없습니다.</p>
    <div v-if="selected" class="map-selection" aria-live="polite"><strong>{{ selected.name }}</strong><span>{{ selected.stationId }} · {{ label(riskLabels, selected.risk) }} · 재고 {{ numberText(selected.bikes) }} / {{ numberText(selected.docks) }}</span><a v-if="plan" href="#station-heading">대여소 상세로 이동 ↓</a></div>
    <p v-if="selectedMissing" class="callout">선택한 거점의 좌표가 없어 지도 위치를 표시하지 않습니다. 계획과 사유는 아래 상세에서 확인하세요.</p>
    <p v-else-if="selected && !visibleIds.has(selectedStation)" class="muted">선택한 거점은 현재 검색·필터에서 제외돼 마커가 숨겨져 있습니다.</p>
    <p v-if="data.missingIds.length || data.invalidCount" class="muted">좌표 미제공 계획 거점 {{ data.missingIds.length }}곳 · 좌표 확인 필요 현황 {{ data.invalidCount }}곳. 해당 위치를 추정하거나 연결선을 건너 이어 그리지 않습니다.</p>
    <div class="map-footer"><span>마커 {{ data.points.filter(p => visibleIds.has(p.station.stationId)).length }} / {{ stations.length }}곳 · {{ selectedTruck || '트럭 미선택' }}</span><span>© <a href="https://openfreemap.org/" target="_blank" rel="noopener noreferrer">OpenFreeMap</a> · © <a href="https://www.openmaptiles.org/" target="_blank" rel="noopener noreferrer">OpenMapTiles</a> · © <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors</span></div>
  </section>
</template>
