<script setup lang="ts">
import { computed } from 'vue'
import { label, numberText, reasonText, riskLabels, roleLabels, stationName, timeText, type Plan, type Station } from './model.ts'
const props = defineProps<{ id: string; stations: Map<string, Station>; plan: Plan | null; horizon: number }>()
const station = computed(() => props.stations.get(props.id))
const stop = computed(() => props.plan?.routes.flatMap(r => r.stops).find(s => s.stationId === props.id))
const unserved = computed(() => props.plan?.unserved.find(s => s.stationId === props.id))
</script>

<template>
  <section class="panel detail" aria-labelledby="station-heading">
    <p class="eyebrow">대여소 상세</p>
    <h2 id="station-heading">{{ stationName(id, stations, stop?.stationName ?? unserved?.stationName) }}</h2>
    <p class="muted">{{ id }} · 관측 {{ timeText(station?.observedAt) }} (한국 시간)</p>
    <p v-if="!station" class="callout">이 거점의 대여소 현황이 제공되지 않았습니다. 계획에 담긴 정보만 표시합니다.</p>
    <dl class="facts">
      <div><dt>현재 재고 / 거치대</dt><dd>{{ numberText(station?.bikes) }} / {{ numberText(station?.docks) }}</dd></div>
      <div><dt>{{ horizon }}시간 후 예상 재고</dt><dd>{{ numberText(station?.forecastBikes) }}</dd></div>
      <div><dt>위험 분류</dt><dd>{{ label(riskLabels, station?.risk) }}</dd></div>
      <div><dt>역할 분류</dt><dd>{{ label(roleLabels, station?.role) }}</dd></div>
      <div><dt>고도 (m)</dt><dd>{{ numberText(station?.elevationM) }}</dd></div>
      <div><dt>우선순위 점수</dt><dd>{{ numberText(unserved?.priority) }}</dd></div>
      <div><dt>예상 빈 대여소 시각</dt><dd>미제공</dd></div>
      <div><dt>적용된 제약</dt><dd>미제공</dd></div>
    </dl>
    <div class="evidence"><h3>계획 포함 근거</h3><p>{{ stop?.reason || '미제공' }}</p></div>
    <div v-if="unserved" class="evidence"><h3>미처리 사유</h3><p>{{ reasonText(unserved.reason) }}</p></div>
    <p class="muted">우선순위는 미처리 목록에 제공된 점수만 표시합니다. 예상 재고가 0이어도 빈 대여소 발생 시각으로 해석하지 않습니다.</p>
  </section>
</template>
