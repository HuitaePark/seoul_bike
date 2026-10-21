<!--
위험 거점 지도 (FR-01, FR-06). 지도 타일 없이 위경도를 SVG에 투영한 개략도다.
대여소 색은 위험 종류, 크기는 거치대 수, 선은 트럭별 경로(지난 구간 실선, 남은 구간 점선, 트럭 위치에서 나뉜다)다.
트럭을 고르거나 승인 대기 변경안을 미리 보면(preview) 관련 없는 대여소·경로를 흐리게 한다.
-->
<script setup lang="ts">
import { computed } from 'vue'
import type { Schemas } from '../../shared/api/client'
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
}>()
const emit = defineEmits<{ select: [stationId: string] }>()

const W = 1000
const H = 560
const PAD = 48

const bounds = computed(() => {
  const lats = props.stations.map((s) => s.lat)
  const lons = props.stations.map((s) => s.lon)
  return { minLat: Math.min(...lats), maxLat: Math.max(...lats), minLon: Math.min(...lons), maxLon: Math.max(...lons) }
})

function xy(lat: number, lon: number) {
  const b = bounds.value
  return {
    x: PAD + ((lon - b.minLon) / (b.maxLon - b.minLon)) * (W - PAD * 2),
    y: PAD + ((b.maxLat - lat) / (b.maxLat - b.minLat)) * (H - PAD * 2),
  }
}

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
  props.stations.map((s) => ({ ...s, ...xy(s.lat, s.lon), r: 6 + s.docks / 5, dim: !!lit.value && !lit.value.has(s.stationId) })),
)

const path = (p: [number, number][]) => p.map((q, i) => `${i ? 'L' : 'M'}${q[0].toFixed(1)},${q[1].toFixed(1)}`).join(' ')
const at = (s: { lat: number; lon: number }): [number, number] => {
  const p = xy(s.lat, s.lon)
  return [p.x, p.y]
}

// 트럭마다 지난 구간(첫 정류장~트럭 위치)과 남은 구간(트럭 위치~끝)을 나눠 그린다.
const lines = computed(() =>
  props.routes.map((r) => {
    const ss = r.stops.map((s) => byId.value.get(s.stationId)).filter((s) => !!s)
    const pts = ss.map(at)
    const doneCount = r.stops.filter((s) => s.status === 'DONE').length
    const leg = ss.length === r.stops.length ? legOf(r, props.trucks.find((t) => t.truckId === r.truckId), byId.value) : null
    const split = splitAtTruck(pts, doneCount, leg)
    return {
      truckId: r.truckId,
      done: path(split.done),
      todo: path(split.todo),
      tip: split.tip,
      dim: focusing.value && focus.value !== r.truckId,
    }
  }),
)

// 위치를 아는(정류장을 하나라도 마친) 트럭만 놓는다
const trucks = computed(() => props.trucks.filter(placed).map((t) => ({ ...t, ...xy(t.lat, t.lon), dim: focusing.value && focus.value !== t.truckId })))

// 변경안: 트럭이 지금 있는 곳 → 바뀐 정류장 순서, 빠지는 곳·새로 들르는 곳 표시
const previewLine = computed(() => {
  const p = props.preview
  if (!p?.truckId || !p.after.length) return null
  const t = trucks.value.find((x) => x.truckId === p.truckId)
  const start = lines.value.find((l) => l.truckId === p.truckId)?.tip ?? (t ? at(t) : null)
  const pts = p.after.map((id) => byId.value.get(id)).filter((s) => !!s).map(at)
  return path(start ? [start, ...pts] : pts)
})
const marks = computed(() =>
  previewMarks(props.preview).flatMap((m) => {
    const s = byId.value.get(m.id)
    return s ? [{ ...m, ...xy(s.lat, s.lon), r: 6 + s.docks / 5 }] : []
  }),
)

const riskColor: Record<Schemas['RiskKind'], string> = { EMPTY: 'var(--empty)', OVERFLOW: 'var(--overflow)', NONE: 'var(--ok)' }
</script>

<template>
  <svg :viewBox="`0 0 ${W} ${H}`" class="map" role="img" aria-label="위험 거점 지도">
    <defs>
      <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
        <path d="M40 0H0V40" fill="none" stroke="#e8edf3" stroke-width="1" />
      </pattern>
      <filter id="shadow" x="-50%" y="-50%" width="200%" height="200%">
        <feDropShadow dx="0" dy="2" stdDeviation="3" flood-opacity="0.18" />
      </filter>
    </defs>
    <rect :width="W" :height="H" fill="#f7fafc" />
    <rect :width="W" :height="H" fill="url(#grid)" />
    <!-- 한강·중랑천 (장식) -->
    <path :d="`M0 ${H - 30} C 250 ${H - 70}, 520 ${H - 10}, 760 ${H - 40} S ${W} ${H - 70}, ${W} ${H - 80} V ${H} H 0 Z`" fill="#d4ebf8" />
    <path d="M70 0 C 90 120, 40 260, 120 420 S 180 520, 160 560" fill="none" stroke="#d4ebf8" stroke-width="18" stroke-linecap="round" />
    <text :x="W - 140" :y="H - 22" class="river">한강</text>

    <g v-for="l in lines" :key="l.truckId" :opacity="l.dim ? 0.1 : preview ? 0.55 : 1">
      <path :d="l.done" :stroke="colors[l.truckId]" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0.45" />
      <path :d="l.todo" :stroke="colors[l.truckId]" stroke-width="4" fill="none" stroke-dasharray="2 9" stroke-linecap="round" stroke-linejoin="round" />
    </g>
    <g v-if="previewLine">
      <path :d="previewLine" stroke="#fff" stroke-width="9" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      <path :d="previewLine" stroke="#ea580c" stroke-width="4.5" fill="none" stroke-dasharray="12 6" stroke-linecap="round" stroke-linejoin="round" />
    </g>

    <g
      v-for="p in points"
      :key="p.stationId"
      class="station"
      :class="{ selected: p.stationId === selected }"
      :opacity="p.dim ? 0.2 : 1"
      tabindex="0"
      @click="emit('select', p.stationId)"
      @keydown.enter="emit('select', p.stationId)"
    >
      <circle v-if="p.risk !== 'NONE'" :cx="p.x" :cy="p.y" :r="p.r + 8" :fill="riskColor[p.risk]" opacity="0.16" class="pulse" />
      <circle :cx="p.x" :cy="p.y" :r="p.r" :fill="riskColor[p.risk]" stroke="#fff" stroke-width="2.5" />
      <text :x="p.x" :y="p.y + 4" class="count">{{ p.bikes }}</text>
      <text :x="p.x" :y="p.y - p.r - 7" class="name">{{ p.name }}</text>
      <title>{{ p.name }} · 재고 {{ p.bikes }}/{{ p.docks }} · 예측 {{ p.forecastBikes }}</title>
    </g>

    <g v-for="m in marks" :key="'m' + m.id" :transform="`translate(${m.x},${m.y - m.r - 6})`" :class="['mark', m.kind]">
      <rect x="-26" y="-20" width="52" height="19" rx="9.5" />
      <text y="-6.5">{{ markText[m.kind] }}</text>
    </g>

    <g v-for="t in trucks" :key="t.truckId" :transform="`translate(${t.x},${t.y})`" filter="url(#shadow)" :opacity="t.dim ? 0.3 : 1">
      <rect x="-39" y="-36" width="78" height="22" rx="11" :fill="colors[t.truckId]" />
      <text y="-21" class="truck-label">{{ t.truckId }} {{ t.load }}/{{ t.capacity }}</text>
      <path d="M0 -14 L-6 -6 H6 Z" :fill="colors[t.truckId]" />
      <circle r="6" fill="#fff" :stroke="colors[t.truckId]" stroke-width="3" />
    </g>
  </svg>
</template>

<style scoped>
.map { width: 100%; height: auto; display: block; border-radius: 12px; }
.river { font-size: 13px; fill: #4a9fd6; font-weight: 600; }
.station { cursor: pointer; outline: none; }
.station .name { font-size: 11px; fill: #334155; text-anchor: middle; font-weight: 600; paint-order: stroke; stroke: #f8fafc; stroke-width: 4px; opacity: 0; transition: opacity 0.15s; }
.station:hover .name, .station:focus .name, .station.selected .name { opacity: 1; }
.station.selected > circle:nth-last-of-type(1) { stroke: var(--ink); stroke-width: 3.5; }
.count { font-size: 10px; font-weight: 700; fill: #fff; text-anchor: middle; pointer-events: none; }
.truck-label { font-size: 11px; font-weight: 700; fill: #fff; text-anchor: middle; }
.station { transition: opacity 0.2s; }
.mark { pointer-events: none; }
.mark text { font-size: 11px; font-weight: 800; fill: #fff; text-anchor: middle; }
.mark.removed rect { fill: #dc2626; }
.mark.added rect { fill: #059669; }
.mark.named rect { fill: #ea580c; }
.pulse { animation: pulse 2.4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
@keyframes pulse { 50% { transform: scale(1.35); opacity: 0.05; } }
</style>
