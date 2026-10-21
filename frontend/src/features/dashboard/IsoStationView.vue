<!--
3D 관제 뷰 (FR-01, FR-06). 위경도를 아이소메트릭 땅 위에 놓고 대여소를 기둥으로 세운다.
기둥 높이 = 지금 재고, 투명 상자 = 거치대 수, 점선 테두리 = 3시간 뒤 예측. 색은 위험 종류.
땅 위 선은 트럭 경로(지난 구간 실선, 남은 구간 점선)이고, 트럭이 있는 곳에서 둘이 나뉜다. 건물·강은 위치 감을 주는 장식이다.
트럭 하나를 고르면 그 트럭 경로의 대여소만 밝게 두고 나머지는 흐리게 한다. 승인 대기 변경안을 고르면(preview) 바뀐 경로를 겹쳐 그린다.
-->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import type { Schemas } from '../../shared/api/client'
import type { TruckLive } from '../../shared/dummy'
import { isoProjector, riskShades } from './iso'
import type { RoutePreview } from './route-view'
import { legOf, markText, previewMarks, splitAtTruck } from './route-view'

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

// 땅 크기(격자 칸). 광진·성동은 동서 약 5.8km, 남북 약 3.3km라 가로로 길다.
const GX = 17.5
const GY = 10
const PAD = 1.5
const W = GX + PAD * 2
const D = 14 // 남쪽에 한강 자리를 더 둔다
const RIVER_Y = 12.3
const COL = 0.9 // 기둥 한 변
const PER_BIKE = 0.075 // 자전거 1대당 기둥 높이

const iso = isoProjector(24, 311, 84)
const { xy, poly, flat, box, groundText } = iso
const fmt = (pts: [number, number][]) => pts.map(([a, b]) => `${a.toFixed(1)},${b.toFixed(1)}`).join(' ')

// 밝게 볼 트럭: 변경안 미리보기가 있으면 그 트럭(제약 추가처럼 트럭이 없으면 null), 아니면 골라 본 트럭
const focusing = computed(() => !!props.preview || !!props.focusTruck)
const focus = computed(() => (props.preview ? props.preview.truckId : props.focusTruck ?? null))
const focusRoute = computed(() => props.routes.find((r) => r.truckId === focus.value))
// 밝게 둘 대여소. 고르지 않았으면 null(전부 밝게)
const lit = computed<Set<string> | null>(() => {
  if (!focusing.value) return null
  return new Set([...(focusRoute.value?.stops.map((s) => s.stationId) ?? []), ...(props.preview?.stations ?? []), ...(props.selected ? [props.selected] : [])])
})

const bounds = computed(() => {
  const lats = props.stations.map((s) => s.lat)
  const lons = props.stations.map((s) => s.lon)
  return { minLat: Math.min(...lats), maxLat: Math.max(...lats), minLon: Math.min(...lons), maxLon: Math.max(...lons) }
})
function grid(lat: number, lon: number) {
  const b = bounds.value
  return {
    x: PAD + ((lon - b.minLon) / (b.maxLon - b.minLon || 1)) * GX,
    y: PAD + ((b.maxLat - lat) / (b.maxLat - b.minLat || 1)) * GY,
  }
}

const cols = computed(() =>
  props.stations.map((s) => {
    const g = grid(s.lat, s.lon)
    const x0 = g.x - COL / 2
    const y0 = g.y - COL / 2
    const hDocks = s.docks * PER_BIKE
    const hBikes = Math.max(0.06, s.bikes * PER_BIKE)
    return {
      ...s,
      gx: g.x,
      gy: g.y,
      shades: riskShades[s.risk],
      shadow: flat(x0 - 0.12, y0 - 0.12, COL + 0.24, COL + 0.24),
      ring: flat(x0 - 0.25, y0 - 0.25, COL + 0.5, COL + 0.5, 0.01),
      solid: box(x0, y0, 0, COL, COL, hBikes),
      ghost: box(x0, y0, 0, COL, COL, hDocks),
      forecast: s.forecastBikes == null ? null : flat(x0, y0, COL, COL, s.forecastBikes * PER_BIKE),
      top: xy(g.x, g.y, Math.max(hDocks, (s.forecastBikes ?? 0) * PER_BIKE)),
      dim: !!lit.value && !lit.value.has(s.stationId),
    }
  }),
)

// 장식 건물: 대여소·강과 겹치지 않는 칸에 낮은 흰 상자를 흩뿌린다 (늘 같은 자리)
const buildings = computed(() => {
  const out: { x: number; y: number; w: number; d: number; h: number; depth: number }[] = []
  let i = 0
  for (let y = 0.4; y < RIVER_Y - 1.4; y += 1.7) {
    for (let x = 0.6; x < W - 1.4; x += 1.9) {
      i++
      const r = Math.abs(Math.sin(i * 12.9898) * 43758.5453) % 1
      if (r < 0.55) continue
      const w = 0.8 + (r * 7) % 0.6
      const d = 0.7 + (r * 13) % 0.5
      const near = cols.value.some((c) => Math.abs(c.gx - (x + w / 2)) < 1.3 && Math.abs(c.gy - (y + d / 2)) < 1.3)
      if (near || x < 1.4) continue
      out.push({ x, y, w, d, h: 0.15 + ((r * 31) % 1) * 0.4, depth: x + y + (w + d) / 2 })
    }
  }
  return out
})

const trucks = computed(() =>
  props.trucks.map((t) => {
    const g = grid(t.lat, t.lon)
    return {
      ...t,
      gx: g.x,
      gy: g.y,
      cargo: box(g.x - 0.55, g.y - 0.22, 0.08, 0.75, 0.44, 0.42),
      cab: box(g.x + 0.2, g.y - 0.2, 0.08, 0.32, 0.4, 0.32),
      label: xy(g.x, g.y, 0.9),
      dim: focusing.value && focus.value !== t.truckId,
    }
  }),
)

type Item = { kind: 'col'; depth: number; c: (typeof cols.value)[number] } | { kind: 'bldg'; depth: number; b: (typeof buildings.value)[number] } | { kind: 'truck'; depth: number; t: (typeof trucks.value)[number] }

// 뒤(x+y가 작은 것)에서 앞으로 그린다
const items = computed<Item[]>(() =>
  [
    ...buildings.value.map((b) => ({ kind: 'bldg' as const, depth: b.depth, b })),
    ...cols.value.map((c) => ({ kind: 'col' as const, depth: c.gx + c.gy, c })),
    ...trucks.value.map((t) => ({ kind: 'truck' as const, depth: t.gx + t.gy + 0.3, t })),
  ].sort((a, b) => a.depth - b.depth),
)

const byId = computed(() => new Map(cols.value.map((c) => [c.stationId, c])))
const stationById = computed(() => new Map(props.stations.map((s) => [s.stationId, s])))
// 지난 구간은 첫 정류장부터 트럭이 지금 있는 곳까지 실선, 남은 구간은 트럭부터 점선 (트럭이 구간 중간이면 선도 중간까지 온다)
const lines = computed(() =>
  props.routes.map((r) => {
    const cs = r.stops.map((s) => byId.value.get(s.stationId)).filter((c) => !!c)
    const pts = cs.map((c) => xy(c.gx, c.gy, 0.02))
    const done = r.stops.filter((s) => s.status === 'DONE').length
    const leg = cs.length === r.stops.length ? legOf(r, props.trucks.find((t) => t.truckId === r.truckId), stationById.value) : null
    const split = splitAtTruck(pts, done, leg)
    return {
      truckId: r.truckId,
      done: fmt(split.done),
      todo: fmt(split.todo),
      tip: split.tip,
      dim: focusing.value && focus.value !== r.truckId,
    }
  }),
)

// 고른 트럭의 방문 순서 번호 (마친 곳은 ✓)
const badges = computed(() =>
  props.preview || !focusRoute.value
    ? []
    : focusRoute.value.stops.flatMap((s) => {
        const c = byId.value.get(s.stationId)
        return c ? [{ id: s.stationId, at: c.top, text: s.status === 'DONE' ? '✓' : String(s.seq), done: s.status === 'DONE' }] : []
      }),
)

// 변경안: 트럭이 지금 있는 곳 → 바뀐 정류장 순서. 빠지는 곳과 새로 들르는 곳은 표시를 붙인다.
const previewLine = computed(() => {
  const p = props.preview
  if (!p?.truckId || !p.after.length) return null
  const t = trucks.value.find((x) => x.truckId === p.truckId)
  const start = lines.value.find((l) => l.truckId === p.truckId)?.tip ?? (t ? xy(t.gx, t.gy, 0.02) : null)
  const pts = p.after.map((id) => byId.value.get(id)).filter((c) => !!c).map((c) => xy(c.gx, c.gy, 0.04))
  return fmt(start ? [start, ...pts] : pts)
})
const marks = computed(() =>
  previewMarks(props.preview).flatMap((m) => {
    const c = byId.value.get(m.id)
    return c ? [{ ...m, at: c.top }] : []
  }),
)

const hovered = ref<string | null>(null)
const chips = computed(() => cols.value.filter((c) => c.stationId === props.selected || c.stationId === hovered.value))
const chipText = (c: (typeof cols.value)[number]) => `${c.name} · ${c.bikes}/${c.docks}`

// 이름표 폭은 그려진 글자 길이를 재서 정한다. 글자 수로 어림하면 한글(영문의 두 배 가까운 폭)이 앞뒤로 잘린다.
const svg = ref<SVGSVGElement | null>(null)
const measured = ref<Record<string, number>>({})
const chipWidth = (c: (typeof cols.value)[number]) =>
  measured.value[c.stationId] ?? [...chipText(c)].reduce((w, ch) => w + (/[가-힣]/.test(ch) ? 11.5 : ch === ' ' ? 3.2 : 6.8), 0) + 24
function measureChips() {
  for (const el of svg.value?.querySelectorAll<SVGTextElement>('.chip text') ?? []) {
    const w = Math.ceil(el.getComputedTextLength()) + 24
    if (measured.value[el.dataset.id!] !== w) measured.value[el.dataset.id!] = w
  }
}
// 글꼴이 늦게 오면 폭이 바뀌므로 다 온 뒤에 한 번 더 잰다
onMounted(() => {
  measureChips()
  void document.fonts?.ready.then(measureChips)
})
watch(chips, measureChips, { flush: 'post' })

const gridLines = [
  ...Array.from({ length: Math.floor(W / 2) }, (_, i) => [iso.pt((i + 1) * 2, 0), iso.pt((i + 1) * 2, RIVER_Y)]),
  ...Array.from({ length: Math.floor(RIVER_Y / 2) }, (_, i) => [iso.pt(0, (i + 1) * 2), iso.pt(W, (i + 1) * 2)]),
]
const stream = [iso.pt(1.1, 0, 0.01), iso.pt(0.8, 4, 0.01), iso.pt(1.2, 8, 0.01), iso.pt(0.9, RIVER_Y, 0.01)].join(' ')
const waves = [[3, 13], [7.5, 12.8], [11, 13.3], [15, 12.9], [18.5, 13.2]].map(([x, y]) => [iso.pt(x!, y!), iso.pt(x! + 1, y!)].join(' '))
</script>

<template>
  <svg ref="svg" viewBox="10 16 740 512" class="iso" role="img" aria-label="3D 관제 뷰: 대여소 재고와 트럭 경로">
    <!-- 땅 덩어리 -->
    <polygon :points="poly([0, D, 0], [W, D, 0], [W, D, -0.9], [0, D, -0.9])" fill="#b89b7d" />
    <polygon :points="poly([W, 0, 0], [W, D, 0], [W, D, -0.9], [W, 0, -0.9])" fill="#9a7e63" />
    <polygon :points="flat(0, 0, W, D)" fill="#d7ebd1" />
    <polygon :points="flat(0.25, 0.25, W - 0.5, RIVER_Y - 0.5)" fill="#eef5ec" />
    <polyline v-for="(g, i) in gridLines" :key="'g' + i" :points="g.join(' ')" stroke="#dbe7d8" stroke-width="1" />

    <!-- 한강·중랑천 (장식) -->
    <polygon :points="flat(0, RIVER_Y, W, D - RIVER_Y)" fill="#a5d6f2" />
    <polygon :points="poly([0, D, 0], [W, D, 0], [W, D, -0.3], [0, D, -0.3])" fill="#7fbfe4" />
    <polyline v-for="(w, i) in waves" :key="'w' + i" :points="w" stroke="#fff" stroke-width="2" stroke-linecap="round" opacity="0.8" />
    <polyline :points="stream" fill="none" stroke="#a5d6f2" stroke-width="7" stroke-linecap="round" stroke-linejoin="round" />

    <!-- 땅에 눕힌 지명 -->
    <text :transform="groundText(3.6, 2.6)" class="zone">성동구</text>
    <text :transform="groundText(13.2, 1.6)" class="zone">광진구</text>
    <text :transform="groundText(8.6, 13.45)" class="zone river">한강</text>

    <!-- 트럭 경로 -->
    <g v-for="l in lines" :key="l.truckId" :opacity="l.dim ? 0.08 : preview ? 0.55 : 1">
      <polyline :points="l.done" :stroke="colors[l.truckId]" stroke-width="3.5" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0.45" />
      <polyline :points="l.todo" :stroke="colors[l.truckId]" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 7" class="flow" />
    </g>
    <!-- 변경안 경로: 흰 테두리를 두른 주황 실선 -->
    <g v-if="previewLine" class="preview-line">
      <polyline :points="previewLine" stroke="#fff" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round" />
      <polyline :points="previewLine" stroke="#ea580c" stroke-width="3.5" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="9 5" class="flow" />
    </g>

    <!-- 건물·대여소 기둥·트럭 -->
    <template v-for="(it, i) in items" :key="i">
      <g v-if="it.kind === 'bldg'" :opacity="focusing ? 0.3 : 0.75">
        <template v-for="f in [box(it.b.x, it.b.y, 0, it.b.w, it.b.d, it.b.h)]" :key="'f'">
          <polygon :points="f.top" fill="#f4f7f9" /><polygon :points="f.left" fill="#e1e8ee" /><polygon :points="f.right" fill="#d2dbe4" />
        </template>
      </g>

      <g
        v-else-if="it.kind === 'col'"
        class="col"
        :class="{ sel: it.c.stationId === selected }"
        :opacity="it.c.dim ? 0.16 : 1"
        tabindex="0"
        role="button"
        :aria-label="`${it.c.name} 재고 ${it.c.bikes}/${it.c.docks}`"
        @click="emit('select', it.c.stationId)"
        @keydown.enter="emit('select', it.c.stationId)"
        @mouseenter="hovered = it.c.stationId"
        @mouseleave="hovered = null"
      >
        <polygon v-if="it.c.risk === 'EMPTY'" :points="it.c.ring" fill="none" :stroke="it.c.shades[1]" stroke-width="2" class="pulse" />
        <polygon v-if="it.c.stationId === selected" :points="it.c.ring" fill="rgb(15 157 88 / 0.15)" stroke="#0f9d58" stroke-width="2.5" />
        <polygon :points="it.c.shadow" fill="#0f172a" opacity="0.08" />
        <polygon :points="it.c.solid.left" :fill="it.c.shades[1]" />
        <polygon :points="it.c.solid.right" :fill="it.c.shades[2]" />
        <polygon :points="it.c.solid.top" :fill="it.c.shades[0]" />
        <g class="ghost">
          <polygon :points="it.c.ghost.left" /><polygon :points="it.c.ghost.right" /><polygon :points="it.c.ghost.top" />
        </g>
        <polygon v-if="it.c.forecast" :points="it.c.forecast" fill="none" :stroke="it.c.shades[3]" stroke-width="1.6" stroke-dasharray="3 2" />
        <title>{{ it.c.name }} · 재고 {{ it.c.bikes }}/{{ it.c.docks }} · 3시간 뒤 {{ it.c.forecastBikes }}대</title>
      </g>

      <g v-else :opacity="it.t.dim ? 0.3 : 1">
        <polygon :points="it.t.cargo.left" :fill="colors[it.t.truckId]" />
        <polygon :points="it.t.cargo.right" fill="#0f172a" /><polygon :points="it.t.cargo.right" :fill="colors[it.t.truckId]" opacity="0.75" />
        <polygon :points="it.t.cargo.top" :fill="colors[it.t.truckId]" opacity="0.7" />
        <polygon :points="it.t.cab.left" fill="#e4e9f0" /><polygon :points="it.t.cab.right" fill="#c8d2df" /><polygon :points="it.t.cab.top" fill="#fff" />
      </g>
    </template>

    <!-- 트럭 이름표 -->
    <g v-for="t in trucks" :key="'tl' + t.truckId" :transform="`translate(${t.label[0]} ${t.label[1]})`" :opacity="t.dim ? 0.3 : 1" class="truck-tag">
      <rect x="-29" y="-22" width="58" height="20" rx="10" :fill="colors[t.truckId]" />
      <text y="-8">{{ t.truckId.replace('truck-', 'T') }} {{ t.load }}/{{ t.capacity }}</text>
      <path d="M-5 -2 L0 4 L5 -2 Z" :fill="colors[t.truckId]" />
    </g>

    <!-- 고른 트럭의 방문 순서 -->
    <g v-for="b in badges" :key="'b' + b.id" :transform="`translate(${b.at[0]} ${b.at[1] - 6})`" class="seq">
      <circle r="9" :fill="b.done ? colors[focus!] : '#fff'" :stroke="colors[focus!]" stroke-width="2" />
      <text y="3.5" :fill="b.done ? '#fff' : colors[focus!]">{{ b.text }}</text>
    </g>

    <!-- 변경안 표시 -->
    <g v-for="m in marks" :key="'m' + m.id" :transform="`translate(${m.at[0]} ${m.at[1] - 6})`" :class="['mark', m.kind]">
      <rect x="-24" y="-20" width="48" height="18" rx="9" />
      <text y="-7">{{ markText[m.kind] }}</text>
    </g>

    <!-- 고른·가리킨 대여소 이름표 -->
    <g v-for="c in chips" :key="'c' + c.stationId" :transform="`translate(${c.top[0]} ${c.top[1] - 8})`" class="chip">
      <rect :x="-chipWidth(c) / 2" y="-24" :width="chipWidth(c)" height="22" rx="11" fill="#0f172a" />
      <text y="-9" :data-id="c.stationId">{{ c.name }} · <tspan :fill="c.shades[0]">{{ c.bikes }}/{{ c.docks }}</tspan></text>
    </g>
  </svg>
</template>

<style scoped>
.iso { display: block; width: 100%; height: 100%; font-family: inherit; }
.zone { font-size: 22px; font-weight: 800; fill: #9fbf9f; letter-spacing: 0.1em; opacity: 0.8; }
.zone.river { fill: #fff; opacity: 0.95; font-size: 18px; }
.col { cursor: pointer; outline: none; }
.col:focus-visible .ghost polygon { stroke: #0f9d58; stroke-width: 2; }
.ghost polygon { fill: rgb(255 255 255 / 0.3); stroke: rgb(71 85 105 / 0.7); stroke-width: 0.9; }
.col:hover .ghost polygon, .col.sel .ghost polygon { fill: rgb(255 255 255 / 0.2); stroke: #0f172a; }
.truck-tag text { font-size: 11px; font-weight: 800; fill: #fff; text-anchor: middle; }
.chip { pointer-events: none; }
.chip text { font-size: 11.5px; font-weight: 700; fill: #fff; text-anchor: middle; }
.col { transition: opacity 0.2s; }
.seq { pointer-events: none; }
.seq text { font-size: 10px; font-weight: 800; text-anchor: middle; }
.mark { pointer-events: none; }
.mark text { font-size: 10.5px; font-weight: 800; fill: #fff; text-anchor: middle; }
.mark.removed rect { fill: #dc2626; }
.mark.added rect { fill: #059669; }
.mark.named rect { fill: #ea580c; }
.flow { animation: flow 1.2s linear infinite; }
@keyframes flow { to { stroke-dashoffset: -18; } }
.pulse { animation: pulse 2s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.15; } }
@media (prefers-reduced-motion: reduce) {
  .flow, .pulse { animation: none; }
}
</style>
