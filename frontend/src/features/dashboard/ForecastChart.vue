<!--
위험 대여소의 지금 재고 → 3시간 뒤 예측. 빈 원 = 지금, 색 점 = 예측, 둘을 잇는 선이 변화량이다.
옅은 기둥은 거치대 수(가득 찬 높이)다. 예측이 거치대를 넘으면 과적재, 0에 닿으면 빈 대여소.
-->
<script setup lang="ts">
import { computed } from 'vue'
import type { Schemas } from '../../shared/api/client'
import { riskShades } from './iso'

const props = defineProps<{ stations: Schemas['StationStatus'][]; selected?: string | null }>()
const emit = defineEmits<{ select: [stationId: string] }>()

const W = 380
const H = 210
const L = 26 // 왼쪽 눈금 자리
const B = 150 // 바닥선 y
const T = 22

const max = computed(() => Math.max(10, ...props.stations.flatMap((s) => [s.docks, s.forecastBikes ?? 0, s.bikes])))
const y = (v: number) => B - (v / max.value) * (B - T)
const ticks = computed(() => [0, Math.round(max.value / 2), max.value])

const bars = computed(() => {
  const step = (W - L - 8) / props.stations.length
  return props.stations.map((s, i) => {
    const forecast = s.forecastBikes ?? s.bikes // 예측이 없으면 지금 값에 둔다
    return {
      ...s,
      forecast,
      x: L + step * (i + 0.5),
      color: riskShades[s.risk][1],
      short: s.name.split(' ')[0],
      delta: forecast - s.bikes,
    }
  })
})
</script>

<template>
  <svg :viewBox="`0 0 ${W} ${H}`" class="chart" role="img" aria-label="위험 대여소 3시간 뒤 예측 재고">
    <g class="axis">
      <template v-for="t in ticks" :key="t">
        <line :x1="L" :x2="W - 4" :y1="y(t)" :y2="y(t)" />
        <text :x="L - 6" :y="y(t) + 3">{{ t }}</text>
      </template>
    </g>
    <g
      v-for="b in bars"
      :key="b.stationId"
      class="bar"
      :class="{ sel: b.stationId === selected }"
      tabindex="0"
      role="button"
      :aria-label="`${b.name} 지금 ${b.bikes}대, 3시간 뒤 ${b.forecastBikes ?? '예측 없음'}, 거치대 ${b.docks}`"
      @click="emit('select', b.stationId)"
      @keydown.enter="emit('select', b.stationId)"
    >
      <rect :x="b.x - 17" :y="T - 14" width="34" :height="H - T + 10" rx="8" class="hit" />
      <rect :x="b.x - 7" :y="y(b.docks)" width="14" :height="B - y(b.docks)" rx="4" class="cap" />
      <line :x1="b.x" :x2="b.x" :y1="y(b.bikes)" :y2="y(b.forecast)" :stroke="b.color" stroke-width="3.5" stroke-linecap="round" />
      <circle :cx="b.x" :cy="y(b.bikes)" r="4" fill="#fff" :stroke="b.color" stroke-width="2" />
      <circle :cx="b.x" :cy="y(b.forecast)" r="5.5" :fill="b.color" stroke="#fff" stroke-width="2" />
      <text :transform="`translate(${b.x + 4} ${B + 12}) rotate(-35)`" class="label">{{ b.short }}</text>
      <g v-if="b.stationId === selected" :transform="`translate(${b.x} ${Math.min(y(b.forecast), y(b.bikes), y(b.docks)) - 10})`">
        <rect x="-25" y="-17" width="50" height="18" rx="9" fill="#0f172a" />
        <text y="-4.5" class="tip">{{ b.delta > 0 ? '+' : '' }}{{ b.delta }}대</text>
      </g>
    </g>
  </svg>
</template>

<style scoped>
.chart { display: block; width: 100%; height: auto; font-family: inherit; }
.axis line { stroke: #eef2f6; }
.axis text { font-size: 10px; fill: var(--ink-3); text-anchor: end; }
.bar { cursor: pointer; outline: none; }
.hit { fill: transparent; }
.bar:hover .hit, .bar:focus-visible .hit { fill: #f8fafc; }
.bar.sel .hit { fill: var(--brand-soft); }
.cap { fill: #e8eef3; }
.label { font-size: 10px; fill: var(--ink-2); text-anchor: end; font-weight: 600; }
.tip { font-size: 10.5px; fill: #fff; font-weight: 800; text-anchor: middle; }
</style>
