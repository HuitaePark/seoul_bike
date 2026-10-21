<!--
3D 관제 뷰 (FR-01, FR-06). 위경도를 아이소메트릭 땅 위에 놓고 대여소를 기둥으로 세운다.
기둥 높이 = 지금 재고, 투명 상자 = 거치대 수, 점선 테두리 = 3시간 뒤 예측. 색은 위험 종류.
땅 위 선은 트럭 경로(지난 구간 실선, 남은 구간 점선)이고, 트럭이 있는 곳에서 둘이 나뉜다.
트럭 하나를 고르면 그 트럭 경로의 대여소만 밝게 두고 나머지는 흐리게 한다. 승인 대기 변경안을 고르면(preview) 바뀐 경로를 겹쳐 그린다.
대여소가 수백 곳이라 땅은 실제 거리에 맞춰 넓게 깐다: 1칸 = 50m(K=20칸/km), 기둥 한 변은 약 45m라 대여소 사이(N02 평균 약 330m)가
실제처럼 떨어져 보인다. 땅 격자 한 칸이 몇 m인지는 확대 배수에 맞춰 고르고(100m~1km) 확대 버튼 아래에 적는다. 처음에는 구역 전체를 담아 멀리서 본다.
마우스 휠로 커서 자리를 확대·축소하고, 끌어서 옮긴다. 이름표·트럭 표·경로 선은 확대해도 화면에서 같은 크기다.
멀리서 볼 때는 기둥만 그리고, 거치대 상자는 확대했거나 가리킨·고른 대여소에만 그린다 (DETAIL_ZOOM).
-->
<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Schemas } from '../../shared/api/client'
import { isoProjector, riskShades } from './iso'
import type { RoutePreview, TruckLive } from './route-view'
import { legOf, markText, placed, previewMarks, splitAtTruck } from './route-view'
import { fit, panBy, wheelFactor, zoomAt, zoomOf, type View } from './zoom'

const props = defineProps<{
  stations: Schemas['StationStatus'][]
  routes: Schemas['Route'][]
  trucks: TruckLive[]
  colors: Record<string, string>
  selected?: string | null
  focusTruck?: string | null
  preview?: RoutePreview | null
  /** 이 위험 종류의 대여소만 그린다 (권역 상태 색 버튼). 땅 크기·배치는 그대로다 */
  only?: Schemas['RiskKind'] | null
}>()
const emit = defineEmits<{ select: [stationId: string] }>()

const K = 20 // 1km당 칸 수 (1칸 = 50m). 구역마다 넓이·모양이 달라 고정 칸 수에 늘이면 찌그러지고 대여소가 겹친다
const M_PER_UNIT = 1000 / K
/** 땅 격자 한 칸 후보(m). 화면에서 한 칸이 GRID_MIN_PX 이상인 가장 작은 것을 쓴다 */
const GRID_STEPS_M = [100, 250, 500, 1000]
const GRID_MIN_PX = 22
const PAD = 1.5
const COL = 0.9 // 기둥 한 변
const PER_BIKE = 0.06 // 자전거 1대당 기둥 높이
const UNIT = 24
/** 이 배수보다 확대하면 모든 대여소의 거치대 상자를 그린다 */
const DETAIL_ZOOM = 1.8

const iso = isoProjector(UNIT, 0, 0)
const { xy, poly, flat, box } = iso
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
// 땅 크기(칸): 동서·남북 실제 거리(km) × K. 대여소가 한 곳뿐이어도 땅이 생기게 0.5km는 둔다
const ground = computed(() => {
  const b = bounds.value
  const lat = (((b.minLat + b.maxLat) / 2) * Math.PI) / 180
  const gx = Math.max(0.5, (b.maxLon - b.minLon) * 111.32 * Math.cos(lat)) * K
  const gy = Math.max(0.5, (b.maxLat - b.minLat) * 110.57) * K
  return { gx, gy, w: gx + PAD * 2, d: gy + PAD * 2 }
})
function grid(lat: number, lon: number) {
  const b = bounds.value
  return {
    x: PAD + ((lon - b.minLon) / (b.maxLon - b.minLon || 1)) * ground.value.gx,
    y: PAD + ((b.maxLat - lat) / (b.maxLat - b.minLat || 1)) * ground.value.gy,
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

// 위치를 아는 트럭만 놓는다 (트럭 상태 API: 마지막으로 끝낸 정류장, 없으면 차고지).
// 같은 자리(출발 전 차고지 등)의 트럭은 이름표를 위로 쌓아 겹치지 않게 한다 (stack).
const trucks = computed(() => {
  const seen = new Map<string, number>()
  return props.trucks.filter(placed).map((t) => {
    const g = grid(t.lat, t.lon)
    const at = `${g.x.toFixed(2)},${g.y.toFixed(2)}`
    const stack = seen.get(at) ?? 0
    seen.set(at, stack + 1)
    return {
      ...t,
      gx: g.x,
      gy: g.y,
      stack,
      cargo: box(g.x - 0.55, g.y - 0.22, 0.08, 0.75, 0.44, 0.42),
      cab: box(g.x + 0.2, g.y - 0.2, 0.08, 0.32, 0.4, 0.32),
      label: xy(g.x, g.y, 0.9),
      dim: focusing.value && focus.value !== t.truckId,
    }
  })
})

type Item = { kind: 'col'; depth: number; c: (typeof cols.value)[number] } | { kind: 'truck'; depth: number; t: (typeof trucks.value)[number] }

// 뒤(x+y가 작은 것)에서 앞으로 그린다. 위험 종류를 골랐으면 그 대여소만
const items = computed<Item[]>(() =>
  [
    ...cols.value.filter((c) => !props.only || c.risk === props.only).map((c) => ({ kind: 'col' as const, depth: c.gx + c.gy, c })),
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


// ── 확대·축소·이동 ──
// 처음 보기: 땅과 가장 높은 기둥(+ 이름표 자리)을 담는다. 사람이 움직이기 전(view null)에는 자료가 바뀌면 따라간다.
const home = computed<View>(() => {
  const { w, d } = ground.value
  const corners = [xy(0, 0), xy(w, 0), xy(0, d), xy(w, d, -0.9)]
  const tops = cols.value.map((c) => c.top[1])
  return fit(
    {
      minX: Math.min(...corners.map((p) => p[0])),
      maxX: Math.max(...corners.map((p) => p[0])),
      minY: Math.min(...corners.map((p) => p[1]), ...tops) - 30,
      maxY: Math.max(...corners.map((p) => p[1])),
    },
    UNIT,
  )
})
const view = ref<View | null>(null)
const v = computed(() => view.value ?? home.value)
const zoom = computed(() => zoomOf(v.value, home.value))
const detail = computed(() => zoom.value >= DETAIL_ZOOM)

// 그림 1칸이 화면 몇 px인지의 역수 (이름표·선을 화면에서 같은 크기로 그릴 때 곱한다). viewBox는 meet이라 더 큰 쪽 비율이다.
const size = ref({ w: 1, h: 1 })
const k = computed(() => Math.max(v.value.w / size.value.w, v.value.h / size.value.h))
let resize: ResizeObserver | null = null

// 땅 격자: 한 칸(gridStep m)의 한 변이 화면에서 GRID_MIN_PX 이상인 가장 작은 간격. 멀리서는 넓게, 확대하면 100m까지 촘촘하게
const gridStep = computed(() => GRID_STEPS_M.find((m) => ((m / M_PER_UNIT) * UNIT) / k.value >= GRID_MIN_PX) ?? GRID_STEPS_M.at(-1)!)
const gridLines = computed(() => {
  const { w, d } = ground.value
  const u = gridStep.value / M_PER_UNIT
  return [
    ...Array.from({ length: Math.floor(w / u) }, (_, i) => [iso.pt((i + 1) * u, 0), iso.pt((i + 1) * u, d)]),
    ...Array.from({ length: Math.floor(d / u) }, (_, i) => [iso.pt(0, (i + 1) * u), iso.pt(w, (i + 1) * u)]),
  ]
})
const gridLabel = computed(() => (gridStep.value >= 1000 ? `${gridStep.value / 1000}km` : `${gridStep.value}m`))

const svg = ref<SVGSVGElement | null>(null)
/** 화면 좌표 → 그림 좌표 */
function toSvg(cx: number, cy: number) {
  const m = svg.value?.getScreenCTM()
  if (!m) return { x: v.value.x + v.value.w / 2, y: v.value.y + v.value.h / 2 }
  const p = new DOMPoint(cx, cy).matrixTransform(m.inverse())
  return { x: p.x, y: p.y }
}

function setView(next: View) {
  view.value = next === home.value ? null : next
}

// 휠: 커서 자리를 고정하고 확대·축소한다. 더 줄일 수 없을 때(전체 보기에서 축소)는 페이지가 그대로 스크롤된다.
function onWheel(e: WheelEvent) {
  const f = wheelFactor(e.deltaY, e.deltaMode)
  if ((f < 1 && !view.value) || f === 1) return
  e.preventDefault()
  const p = toSvg(e.clientX, e.clientY)
  setView(zoomAt(v.value, f, p.x, p.y, home.value))
}

function zoomBy(f: number) {
  setView(zoomAt(v.value, f, v.value.x + v.value.w / 2, v.value.y + v.value.h / 2, home.value))
}

// 끌기: 4px 넘게 움직여야 끌기로 본다. 그 전에는 대여소 누르기(선택)가 그대로 된다.
const dragging = ref(false)
let drag: { id: number; x: number; y: number; from: View; moved: boolean } | null = null
let swallowClick = false
function onDown(e: PointerEvent) {
  swallowClick = false
  // 전체 보기에서는 옮길 곳이 없다 (끌면 지도가 화면 밖으로 밀리기만 한다)
  if (e.button !== 0 || !view.value) return
  drag = { id: e.pointerId, x: e.clientX, y: e.clientY, from: v.value, moved: false }
}
function onMove(e: PointerEvent) {
  if (!drag || e.pointerId !== drag.id) return
  const dx = e.clientX - drag.x
  const dy = e.clientY - drag.y
  if (!drag.moved) {
    if (Math.hypot(dx, dy) < 4) return
    drag.moved = true
    dragging.value = true
    svg.value?.setPointerCapture(e.pointerId)
  }
  setView(panBy(drag.from, dx * k.value, dy * k.value, home.value))
}
function onUp() {
  if (drag?.moved) swallowClick = true
  drag = null
  dragging.value = false
}
// 끈 뒤에 오는 클릭은 대여소 선택으로 받지 않는다
function onClickCapture(e: MouseEvent) {
  if (!swallowClick) return
  swallowClick = false
  e.stopPropagation()
}

onMounted(() => {
  resize = new ResizeObserver(([entry]) => {
    if (entry) size.value = { w: entry.contentRect.width || 1, h: entry.contentRect.height || 1 }
  })
  if (svg.value) resize.observe(svg.value)
})
onBeforeUnmount(() => resize?.disconnect())
</script>

<template>
  <div class="iso-wrap">
    <svg
      ref="svg"
      :viewBox="`${v.x.toFixed(1)} ${v.y.toFixed(1)} ${v.w.toFixed(1)} ${v.h.toFixed(1)}`"
      :class="['iso', { dragging, detail, zoomed: !!view }]"
      role="img"
      aria-label="3D 관제 뷰: 대여소 재고와 트럭 경로. 마우스 휠로 확대·축소, 끌어서 이동"
      :data-zoom="zoom.toFixed(2)"
      :data-grid-m="gridStep"
      @wheel="onWheel"
      @pointerdown="onDown"
      @pointermove="onMove"
      @pointerup="onUp"
      @pointercancel="onUp"
      @click.capture="onClickCapture"
    >
      <!-- 땅 덩어리 -->
      <polygon :points="poly([0, ground.d, 0], [ground.w, ground.d, 0], [ground.w, ground.d, -0.9], [0, ground.d, -0.9])" fill="#b89b7d" />
      <polygon :points="poly([ground.w, 0, 0], [ground.w, ground.d, 0], [ground.w, ground.d, -0.9], [ground.w, 0, -0.9])" fill="#9a7e63" />
      <polygon :points="flat(0, 0, ground.w, ground.d)" fill="#d7ebd1" />
      <polygon :points="flat(0.25, 0.25, ground.w - 0.5, ground.d - 0.5)" fill="#eef5ec" />
      <polyline v-for="(g, i) in gridLines" :key="'g' + i" :points="g.join(' ')" stroke="#dbe7d8" stroke-width="1" vector-effect="non-scaling-stroke" />

      <!-- 트럭 경로 (확대해도 선 굵기는 화면에서 같다) -->
      <g v-for="l in lines" :key="l.truckId" :opacity="l.dim ? 0.08 : preview ? 0.55 : 1">
        <polyline :points="l.done" :stroke="colors[l.truckId]" stroke-width="3.5" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity="0.45" vector-effect="non-scaling-stroke" />
        <polyline :points="l.todo" :stroke="colors[l.truckId]" stroke-width="3" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 7" class="flow" vector-effect="non-scaling-stroke" />
      </g>
      <!-- 변경안 경로: 흰 테두리를 두른 주황 실선 -->
      <g v-if="previewLine" class="preview-line">
        <polyline :points="previewLine" stroke="#fff" stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke" />
        <polyline :points="previewLine" stroke="#ea580c" stroke-width="3.5" fill="none" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="9 5" class="flow" vector-effect="non-scaling-stroke" />
      </g>

      <!-- 대여소 기둥·트럭 -->
      <template v-for="(it, i) in items" :key="i">
        <g
          v-if="it.kind === 'col'"
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
          <polygon v-if="it.c.risk === 'EMPTY'" :points="it.c.ring" fill="none" :stroke="it.c.shades[1]" stroke-width="2" class="pulse" vector-effect="non-scaling-stroke" />
          <polygon v-if="it.c.stationId === selected" :points="it.c.ring" fill="rgb(15 157 88 / 0.15)" stroke="#0f9d58" stroke-width="2.5" vector-effect="non-scaling-stroke" />
          <polygon :points="it.c.shadow" fill="#0f172a" opacity="0.08" />
          <polygon :points="it.c.solid.left" :fill="it.c.shades[1]" />
          <polygon :points="it.c.solid.right" :fill="it.c.shades[2]" />
          <polygon :points="it.c.solid.top" :fill="it.c.shades[0]" />
          <!-- 거치대 상자·예측은 확대했거나 가리킨·고른 대여소에만 (멀리서는 기둥만) -->
          <template v-if="detail || it.c.stationId === selected || it.c.stationId === hovered">
            <g class="ghost">
              <polygon :points="it.c.ghost.left" /><polygon :points="it.c.ghost.right" /><polygon :points="it.c.ghost.top" />
            </g>
            <polygon v-if="it.c.forecast" :points="it.c.forecast" fill="none" :stroke="it.c.shades[3]" stroke-width="1.6" stroke-dasharray="3 2" vector-effect="non-scaling-stroke" />
          </template>
          <title>{{ it.c.name }} · 재고 {{ it.c.bikes }}/{{ it.c.docks }} · 3시간 뒤 {{ it.c.forecastBikes ?? '예측 없음' }}</title>
        </g>

        <g v-else :opacity="it.t.dim ? 0.3 : 1">
          <polygon :points="it.t.cargo.left" :fill="colors[it.t.truckId]" />
          <polygon :points="it.t.cargo.right" fill="#0f172a" /><polygon :points="it.t.cargo.right" :fill="colors[it.t.truckId]" opacity="0.75" />
          <polygon :points="it.t.cargo.top" :fill="colors[it.t.truckId]" opacity="0.7" />
          <polygon :points="it.t.cab.left" fill="#e4e9f0" /><polygon :points="it.t.cab.right" fill="#c8d2df" /><polygon :points="it.t.cab.top" fill="#fff" />
        </g>
      </template>

      <!-- 이름표들은 scale(k)로 화면에서 같은 크기를 지킨다 -->
      <!-- 트럭 이름표 -->
      <g v-for="t in trucks" :key="'tl' + t.truckId" :transform="`translate(${t.label[0]} ${t.label[1]}) scale(${k}) translate(0 ${-24 * t.stack})`" :opacity="t.dim ? 0.3 : 1" class="truck-tag">
        <rect x="-38" y="-22" width="76" height="20" rx="10" :fill="colors[t.truckId]" />
        <text y="-8">{{ t.truckId }} {{ t.load }}/{{ t.capacity }}</text>
        <path d="M-5 -2 L0 4 L5 -2 Z" :fill="colors[t.truckId]" />
      </g>

      <!-- 고른 트럭의 방문 순서 -->
      <g v-for="b in badges" :key="'b' + b.id" :transform="`translate(${b.at[0]} ${b.at[1]}) scale(${k}) translate(0 -6)`" class="seq">
        <circle r="9" :fill="b.done ? colors[focus!] : '#fff'" :stroke="colors[focus!]" stroke-width="2" />
        <text y="3.5" :fill="b.done ? '#fff' : colors[focus!]">{{ b.text }}</text>
      </g>

      <!-- 변경안 표시 -->
      <g v-for="m in marks" :key="'m' + m.id" :transform="`translate(${m.at[0]} ${m.at[1]}) scale(${k}) translate(0 -6)`" :class="['mark', m.kind]">
        <rect x="-24" y="-20" width="48" height="18" rx="9" />
        <text y="-7">{{ markText[m.kind] }}</text>
      </g>

      <!-- 고른·가리킨 대여소 이름표 -->
      <g v-for="c in chips" :key="'c' + c.stationId" :transform="`translate(${c.top[0]} ${c.top[1]}) scale(${k}) translate(0 -8)`" class="chip">
        <rect :x="-chipWidth(c) / 2" y="-24" :width="chipWidth(c)" height="22" rx="11" fill="#0f172a" />
        <text y="-9" :data-id="c.stationId">{{ c.name }} · <tspan :fill="c.shades[0]">{{ c.bikes }}/{{ c.docks }}</tspan></text>
      </g>
    </svg>

    <div class="zoom-ctl" role="group" aria-label="3D 관제 뷰 확대·축소">
      <button type="button" aria-label="확대" title="확대 (마우스 휠 위로)" :disabled="zoom >= 7.99" @click="zoomBy(1.6)">+</button>
      <button type="button" aria-label="축소" title="축소 (마우스 휠 아래로)" :disabled="!view" @click="zoomBy(1 / 1.6)">−</button>
      <button type="button" class="home" aria-label="구역 전체 보기" title="구역 전체 보기" :disabled="!view" @click="view = null">전체</button>
      <span class="zoom-level" aria-live="polite">{{ Math.round(zoom * 100) }}%</span>
      <span class="grid-scale" :title="`땅 격자 한 칸의 한 변이 실제 ${gridLabel}다 (1칸 = ${M_PER_UNIT}m 기준으로 그린다)`">
        <i aria-hidden="true" />한 칸<br /><b>{{ gridLabel }}</b>
      </span>
    </div>
  </div>
</template>

<style scoped>
.iso-wrap { position: relative; display: flex; min-height: 0; }
.iso { display: block; flex: 1; width: 100%; height: 100%; min-height: 420px; font-family: inherit; user-select: none; }
.iso.zoomed { cursor: grab; }
.iso.dragging { cursor: grabbing; }
.iso.dragging .col { cursor: grabbing; }
.zoom-ctl { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 4px; background: rgb(255 255 255 / 0.94); border: 1px solid var(--line); border-radius: 12px; box-shadow: var(--shadow); z-index: 2; }
.zoom-ctl button { width: 32px; height: 32px; padding: 0; font-size: 18px; font-weight: 700; line-height: 1; color: var(--ink); background: #fff; border: 1px solid var(--line); border-radius: 8px; }
.zoom-ctl button:hover:not(:disabled) { background: #f1f5f9; }
.zoom-ctl button:disabled { color: var(--ink-3); opacity: 0.5; }
.zoom-ctl .home { font-size: 11px; }
.zoom-level { font-size: 10.5px; font-weight: 700; color: var(--ink-3); font-variant-numeric: tabular-nums; }
.grid-scale { display: flex; flex-direction: column; align-items: center; padding-top: 4px; border-top: 1px solid var(--line); font-size: 10px; line-height: 1.25; color: var(--ink-3); text-align: center; }
.grid-scale b { font-size: 11px; color: var(--ink); }
.grid-scale i { width: 14px; height: 9px; margin-bottom: 2px; border: 1.5px solid #9fb59a; transform: skewX(-30deg) scaleY(0.6); }
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
