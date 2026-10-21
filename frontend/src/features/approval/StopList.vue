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
        {{ s.stationId }} {{ actionLabel[s.action] }} {{ s.bikes }}대
      </button>
      <span v-else :class="['stop', s.action, marks[s.stationId]]">
        {{ s.stationId }} {{ actionLabel[s.action] }} {{ s.bikes }}대
      </span>
    </li>
  </ol>
  <span v-else class="muted">정류장 없음</span>
</template>

<style scoped>
.stops { list-style: none; display: flex; flex-wrap: wrap; gap: 6px; padding: 0; margin: 4px 0; }
.stop { display: inline-block; padding: 3px 8px; border-radius: 6px; font-size: 12px; border: 1px solid #d1d5db; background: #fff; color: #111827; }
button.stop { cursor: pointer; }
.stop.PICKUP { border-left: 4px solid #0ea5e9; }
.stop.DROPOFF { border-left: 4px solid #22c55e; }
.stop.removed { text-decoration: line-through; color: #b91c1c; }
.stop.added { background: #ecfdf5; }
.stop.picked { background: #fee2e2; border-color: #b91c1c; text-decoration: line-through; }
</style>
