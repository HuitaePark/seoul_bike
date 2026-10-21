<!--
트럭 적재 그림. 짐칸을 투명 상자로 그리고 안을 실은 대수만큼 채운다 (FR-06).
-->
<script setup lang="ts">
import { computed } from 'vue'
import { isoProjector } from './iso'

const props = defineProps<{ load: number; capacity: number; color: string }>()

const iso = isoProjector(34, 130, 100)
const { box, sideText } = iso

// 짐칸 x 0..3.4, 운전석 x 3.4..4.5 (+x가 앞)
const CARGO = { x: 0, y: 0, z: 0.45, w: 3.4, d: 1.5, h: 1.75 }
const ratio = computed(() => (props.capacity ? Math.min(1, props.load / props.capacity) : 0))
const shell = box(CARGO.x, CARGO.y, CARGO.z, CARGO.w, CARGO.d, CARGO.h)
const fill = computed(() => box(CARGO.x + 0.06, CARGO.y + 0.06, CARGO.z, CARGO.w - 0.12, CARGO.d - 0.12, Math.max(0.04, (CARGO.h - 0.06) * ratio.value)))
const chassis = box(-0.05, 0.05, 0.3, 4.6, 1.4, 0.18)
const cab = box(3.45, 0.1, 0.45, 1.05, 1.3, 1.3)
const windshield = iso.poly([4.5, 0.25, 1.05], [4.5, 1.25, 1.05], [4.5, 1.25, 1.6], [4.5, 0.25, 1.6])
const sideWindow = iso.poly([3.6, 1.4, 1.05], [4.2, 1.4, 1.05], [4.2, 1.4, 1.55], [3.6, 1.4, 1.55])
// 바퀴는 왼쪽 면(y+d 면)에 붙는다. 화면에 똑바로 선 타원이 아니라 그 면 기울기대로 비스듬히 누운 원으로 그린다.
const wheels = [0.6, 1.4, 3.9].map((x) => sideText(x, 1.45, 0.28))
const shadow = iso.flat(-0.3, -0.1, 5.2, 1.9)
const pctAt = sideText(0.55, CARGO.d, CARGO.z + CARGO.h * 0.62)
</script>

<template>
  <svg viewBox="0 0 330 228" class="truck" role="img" :aria-label="`트럭 적재 ${load}/${capacity}대`">
    <polygon :points="shadow" fill="#0f172a" opacity="0.07" />
    <polygon :points="chassis.left" fill="#475569" /><polygon :points="chassis.right" fill="#334155" /><polygon :points="chassis.top" fill="#64748b" />
    <g v-for="(w, i) in wheels" :key="i" :transform="w">
      <circle r="11" fill="#1e293b" /><circle r="4.5" fill="#94a3b8" />
    </g>

    <!-- 짐칸: 실은 만큼 채움 + 투명 껍데기 -->
    <polygon :points="fill.left" :fill="color" />
    <polygon :points="fill.right" fill="#0f172a" /><polygon :points="fill.right" :fill="color" opacity="0.75" />
    <polygon :points="fill.top" :fill="color" opacity="0.55" />
    <g class="shell">
      <polygon :points="shell.left" /><polygon :points="shell.right" /><polygon :points="shell.top" />
    </g>
    <text :transform="pctAt" class="pct">{{ Math.round(ratio * 100) }}%</text>

    <!-- 운전석 -->
    <polygon :points="cab.left" fill="#e4e9f0" /><polygon :points="cab.right" fill="#c8d2df" /><polygon :points="cab.top" fill="#ffffff" />
    <polygon :points="windshield" fill="#1e3a5f" /><polygon :points="sideWindow" fill="#1e3a5f" />
  </svg>
</template>

<style scoped>
.truck { display: block; width: 100%; height: auto; }
.shell polygon { fill: rgb(255 255 255 / 0.28); stroke: #94a3b8; stroke-width: 1.2; stroke-linejoin: round; }
.pct { font-size: 30px; font-weight: 800; fill: #0f172a; letter-spacing: -0.02em; }
</style>
