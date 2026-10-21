<script setup lang="ts">
import type { Schemas } from '../../shared/api/client'
import { actionLabel } from '../approval/approval'
import { reasonText } from '../plan-map/model'
import { riskShades } from './iso'

// compact: 관제 뷰의 재고 현황 칸(지도 바로 옆)에 넣는 모양. 카드 테두리 없이 좁은 폭에 맞추고 선택을 풀 수 있다 (#74).
defineProps<{
  station: Schemas['StationStatus']
  stop: (Schemas['Stop'] & { truckId: string }) | null
  unserved?: Schemas['Unserved']
  colors: Record<string, string>
  compact?: boolean
}>()
const emit = defineEmits<{ close: [] }>()

const riskLabel: Record<Schemas['RiskKind'], string> = { EMPTY: '빈 대여소 위험', OVERFLOW: '과적재 위험', NONE: '정상' }
const roleLabel: Record<Schemas['StationRole'], string> = { FIXED_PICKUP: '고정 회수', FIXED_DROPOFF: '고정 배치', VARIABLE: '변동' }
</script>

<template>
  <section :class="['w-detail station-summary', compact ? 'compact' : 'card']">
    <header class="w-head">
      <h2>선택한 대여소</h2>
      <span class="muted">{{ station.stationId }}</span>
      <button v-if="compact" class="close" type="button" aria-label="대여소 선택 해제" @click="emit('close')">✕</button>
    </header>
    <div class="st-head">
      <span class="st-icon" :style="{ background: riskShades[station.risk][1] }" aria-hidden="true">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="5.5" cy="17" r="3.5" /><circle cx="18.5" cy="17" r="3.5" /><path d="M5.5 17 9 9h6l3.5 8M9 9 12 17h1.5M14 5h2.5" /></svg>
      </span>
      <div>
        <strong>{{ station.name }}</strong>
        <div class="st-chips">
          <span :class="['tag', station.risk]">{{ riskLabel[station.risk] }}</span>
          <span class="tag">{{ station.role ? roleLabel[station.role] : '역할 없음' }}</span>
        </div>
      </div>
    </div>
    <dl class="st-facts">
      <div><dt>지금 재고</dt><dd>{{ station.bikes }}대</dd></div>
      <div><dt>거치대</dt><dd>{{ station.docks }}개</dd></div>
      <div><dt>3시간 뒤</dt><dd>{{ station.forecastBikes ?? '-' }}대</dd></div>
      <div><dt>고도</dt><dd>{{ station.elevationM }}m</dd></div>
    </dl>
    <div class="stock-bar"><span :style="{ width: `${Math.min(100, (station.bikes / station.docks) * 100)}%`, background: riskShades[station.risk][1] }" /></div>
    <div v-if="stop" class="plan-note" :style="{ '--c': colors[stop.truckId] }">
      <strong>{{ stop.truckId }} · {{ stop.seq }}번째 · {{ actionLabel[stop.action] }} {{ stop.bikes }}대</strong>
      <span>{{ stop.reason }}</span>
    </div>
    <div v-else-if="unserved" class="plan-note dropped">
      <strong>이번 계획에서 빠짐 · 우선순위 {{ unserved.priority.toFixed(2) }}</strong>
      <span>{{ reasonText(unserved.reason) }}</span>
    </div>
    <p v-else class="muted">이번 계획에 들어가지 않았다</p>
  </section>
</template>

<style scoped>
.w-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.w-head h2 { margin: 0; }
.st-head { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
.st-icon { flex: none; width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center; }
.st-head strong { font-size: 16px; }
.st-chips { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
.tag { padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; background: #eef2f6; color: var(--ink-2); }
.tag.NONE { background: var(--brand-soft); color: var(--brand-strong); }
.tag.EMPTY { background: var(--empty-soft); color: #b91c1c; }
.tag.OVERFLOW { background: var(--overflow-soft); color: #92400e; }
.st-facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 0 0 10px; }
.st-facts div { padding: 8px 10px; background: #f8fafc; border-radius: 10px; min-width: 0; }
dt { color: var(--ink-3); font-size: 11px; font-weight: 600; }
dd { margin: 2px 0 0; font-weight: 700; font-size: 13px; }
.stock-bar { height: 8px; background: #eef2f6; border-radius: 999px; overflow: hidden; margin-bottom: 12px; }
.stock-bar span { display: block; height: 100%; border-radius: 999px; }
/* 트럭 색은 칸 바탕·테두리에 옅게 깐다 */
.plan-note { border: 1px solid color-mix(in srgb, var(--c) 28%, #fff); background: color-mix(in srgb, var(--c) 6%, #fff); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.plan-note.dropped { --c: var(--ink-3); }
.close { margin-left: auto; width: 24px; height: 24px; padding: 0; display: grid; place-items: center; background: transparent; color: var(--ink-3); border-radius: 8px; font-size: 13px; }
.close:hover { background: #f1f5f9; color: var(--ink); }
.compact { padding-bottom: 12px; border-bottom: 1px solid var(--line); }
.compact .w-head { justify-content: flex-start; margin-bottom: 10px; flex-wrap: nowrap; }
.compact .w-head .muted { font-size: 11px; }
.compact .st-head { gap: 10px; margin-bottom: 10px; }
.compact .st-icon { width: 36px; height: 36px; border-radius: 11px; }
.compact .st-head strong { font-size: 14px; }
.compact .st-facts { grid-template-columns: 1fr 1fr; }
.compact .st-facts div { padding: 6px 8px; }
.compact .plan-note { font-size: 12px; padding: 8px 10px; }
@media (max-width: 860px) { .st-facts { grid-template-columns: 1fr 1fr; } }
@media (max-width: 860px) and (min-width: 521px) { .compact .st-facts { grid-template-columns: repeat(4, 1fr); } }
</style>
