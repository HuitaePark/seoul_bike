<!-- 원형 게이지. 가운데 내용은 slot으로 받는다 (기본: 백분율). -->
<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{ value: number; size?: number; stroke?: number; color?: string; track?: string }>(), {
  size: 44,
  stroke: 5,
  color: 'var(--brand)',
  track: '#e8eef3',
})
const r = computed(() => (props.size - props.stroke) / 2)
const len = computed(() => 2 * Math.PI * r.value)
const ratio = computed(() => Math.min(1, Math.max(0, props.value)))
</script>

<template>
  <span class="ring" :style="{ width: `${size}px`, height: `${size}px` }">
    <svg :width="size" :height="size" :viewBox="`0 0 ${size} ${size}`" aria-hidden="true">
      <circle :cx="size / 2" :cy="size / 2" :r="r" fill="none" :stroke="track" :stroke-width="stroke" />
      <circle
        :cx="size / 2"
        :cy="size / 2"
        :r="r"
        fill="none"
        :stroke="color"
        :stroke-width="stroke"
        stroke-linecap="round"
        :stroke-dasharray="`${len * ratio} ${len}`"
        :transform="`rotate(-90 ${size / 2} ${size / 2})`"
      />
    </svg>
    <span class="center"><slot>{{ Math.round(ratio * 100) }}%</slot></span>
  </span>
</template>

<style scoped>
.ring { position: relative; display: inline-grid; place-items: center; flex: none; }
.ring svg { position: absolute; inset: 0; }
.ring svg circle:last-child { transition: stroke-dasharray 0.4s ease; }
.center { position: relative; font-weight: 800; font-size: 12px; color: var(--ink); text-align: center; line-height: 1.1; }
</style>
