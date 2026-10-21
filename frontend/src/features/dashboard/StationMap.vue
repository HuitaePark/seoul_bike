<!--
위험 거점 지도 (FR-01, FR-06). 지도 타일 없이 위경도를 SVG에 투영한 개략도다.
대여소 색은 위험 종류, 크기는 거치대 수, 선은 트럭별 경로(지난 구간 실선, 남은 구간 점선)다.
-->
<script setup lang="ts">
import { computed } from 'vue'
import type { Schemas } from '../../shared/api/client'
import type { TruckLive } from '../../shared/dummy'

const props = defineProps<{
  stations: Schemas['StationStatus'][]
  routes: Schemas['Route'][]
  trucks: TruckLive[]
  colors: Record<string, string>
  selected?: string | null
  focusTruck?: string | null
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

const points = computed(() =>
  props.stations.map((s) => ({ ...s, ...xy(s.lat, s.lon), r: 6 + s.docks / 5 })),
)

// 트럭마다 지난 구간(첫 정류장~마지막 완료)과 남은 구간을 나눠 그린다.
const lines = computed(() =>
  props.routes.map((r) => {
    const pts = r.stops.map((s) => byId.value.get(s.stationId)).filter((s) => !!s).map((s) => xy(s.lat, s.lon))
    const doneCount = r.stops.filter((s) => s.status === 'DONE').length
    const path = (p: { x: number; y: number }[]) => p.map((q, i) => `${i ? 'L' : 'M'}${q.x.toFixed(1)},${q.y.toFixed(1)}`).join(' ')
    return {
      truckId: r.truckId,
      done: path(pts.slice(0, Math.max(doneCount, 1))),
      todo: path(pts.slice(Math.max(doneCount - 1, 0))),
      dim: !!props.focusTruck && props.focusTruck !== r.truckId,
    }
  }),
)

const trucks = computed(() => props.trucks.map((t) => ({ ...t, ...xy(t.lat, t.lon) })))

const riskColor: Record<Schemas['RiskKind'], string> = { EMPTY: 'var(--empty)', OVERFLOW: 'var(--overflow)', NONE: '#94a3b8' }
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
    <rect :width="W" :height="H" fill="#f8fafc" />
    <rect :width="W" :height="H" fill="url(#grid)" />
    <!-- 한강·중랑천 (장식) -->
    <path :d="`M0 ${H - 30} C 250 ${H - 70}, 520 ${H - 10}, 760 ${H - 40} S ${W} ${H - 70}, ${W} ${H - 80} V ${H} H 0 Z`" fill="#dbeafe" />
    <path d="M70 0 C 90 120, 40 260, 120 420 S 180 520, 160 560" fill="none" stroke="#dbeafe" stroke-width="18" stroke-linecap="round" />
    <text :x="W - 140" :y="H - 22" class="river">한강</text>

    <g v-for="l in lines" :key="l.truckId" :opacity="l.dim ? 0.15 : 1">
      <path :d="l.done" :stroke="colors[l.truckId]" stroke-width="5" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0.45" />
      <path :d="l.todo" :stroke="colors[l.truckId]" stroke-width="4" fill="none" stroke-dasharray="2 9" stroke-linecap="round" stroke-linejoin="round" />
    </g>

    <g
      v-for="p in points"
      :key="p.stationId"
      class="station"
      :class="{ selected: p.stationId === selected }"
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

    <g v-for="t in trucks" :key="t.truckId" :transform="`translate(${t.x},${t.y})`" filter="url(#shadow)" :opacity="focusTruck && focusTruck !== t.truckId ? 0.3 : 1">
      <rect x="-30" y="-36" width="60" height="22" rx="11" :fill="colors[t.truckId]" />
      <text y="-21" class="truck-label">{{ t.truckId.replace('truck-', 'T') }} {{ t.load }}/{{ t.capacity }}</text>
      <path d="M0 -14 L-6 -6 H6 Z" :fill="colors[t.truckId]" />
      <circle r="6" fill="#fff" :stroke="colors[t.truckId]" stroke-width="3" />
    </g>
  </svg>
</template>

<style scoped>
.map { width: 100%; height: auto; display: block; border-radius: 10px; }
.river { font-size: 13px; fill: #60a5fa; font-weight: 600; }
.station { cursor: pointer; outline: none; }
.station .name { font-size: 11px; fill: #334155; text-anchor: middle; font-weight: 600; paint-order: stroke; stroke: #f8fafc; stroke-width: 4px; opacity: 0; transition: opacity 0.15s; }
.station:hover .name, .station:focus .name, .station.selected .name { opacity: 1; }
.station.selected > circle:nth-last-of-type(1) { stroke: var(--ink); stroke-width: 3.5; }
.count { font-size: 10px; font-weight: 700; fill: #fff; text-anchor: middle; pointer-events: none; }
.truck-label { font-size: 11px; font-weight: 700; fill: #fff; text-anchor: middle; }
.pulse { animation: pulse 2.4s ease-in-out infinite; transform-box: fill-box; transform-origin: center; }
@keyframes pulse { 50% { transform: scale(1.35); opacity: 0.05; } }
</style>
