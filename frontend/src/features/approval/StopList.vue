<!--
Author: Jung Minkyu
Date: 20OCT2026
Purpose: 트럭 한 대의 정류장 순서 (openapi Stop 목록). 승인 카드에서 '일부 제외'할 대여소를 고르는 데도 쓴다.
-->
<script setup lang="ts">
import type { Stop } from './approval'
import { actionLabel } from './approval'

withDefaults(
  defineProps<{
    stops: Stop[]
    selectable?: boolean
    selected?: string[]
    marks?: Record<string, 'added' | 'removed'>
  }>(),
  { selectable: false, selected: () => [], marks: () => ({}) },
)
const emit = defineEmits<{ toggle: [stationId: string] }>()

// 도착 예정 시각. MCP 응답이 길면 eta가 빠져 오므로 있을 때만 붙인다 (#91: 대시보드는 같은 계획의 경로로 채운다)
const etaText = (s: Stop) =>
  s.eta ? ` · ${new Date(s.eta).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Seoul' })}` : ''
</script>

<template>
  <ol v-if="stops.length" class="stops">
    <li v-for="s in stops" :key="`${s.seq}-${s.stationId}`">
      <button
        v-if="selectable"
        type="button"
        :class="['stop', s.action, marks[s.stationId], { picked: selected.includes(s.stationId) }]"
        :aria-pressed="selected.includes(s.stationId)"
        @click="emit('toggle', s.stationId)"
      >
        {{ s.stationId }} {{ actionLabel[s.action] }} {{ s.bikes }}대{{ etaText(s) }}
      </button>
      <span v-else :class="['stop', s.action, marks[s.stationId]]">
        {{ s.stationId }} {{ actionLabel[s.action] }} {{ s.bikes }}대{{ etaText(s) }}
      </span>
    </li>
  </ol>
  <span v-else class="muted">정류장 없음</span>
</template>

<style scoped>
.stops { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; padding: 0; margin: 4px 0; }
.stop { display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 12px; border: 1px solid #d1d5db; background: #fff; color: #111827; }
button.stop { cursor: pointer; }
/* 회수·배치는 글자에도 있다. 색은 칸 전체(옅은 바탕·테두리)로 보탠다 */
.stop.PICKUP { background: #f0f9ff; border-color: #7dd3fc; }
.stop.DROPOFF { background: #f0fdf4; border-color: #86efac; }
.stop.removed { text-decoration: line-through; color: #b91c1c; }
.stop.added { background: #ecfdf5; }
.stop.picked { background: #fee2e2; border-color: #b91c1c; text-decoration: line-through; }
</style>
