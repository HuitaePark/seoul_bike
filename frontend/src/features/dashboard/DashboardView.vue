<!--
관제 대시보드 (PRD 10장, FR-01~09). 지금은 더미 데이터로 화면만 잡는다.
붙일 API: GET /api/regions/{regionId}/stations, GET /api/shifts/{shiftId}/plan, 작업 SSE의 APPROVAL_REQUIRED.
-->
<script setup lang="ts">
import { reasonText } from '../plan-map/model'
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import type { Schemas } from '../../shared/api/client'
import { DUMMY_PENDING, DUMMY_PLAN, DUMMY_REGION, DUMMY_STATIONS, DUMMY_TRUCKS } from '../../shared/dummy'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'
import { actionLabel, kindLabel } from '../approval/approval'
import StationMap from './StationMap.vue'

const router = useRouter()
const colors: Record<string, string> = { 'truck-1': '#2563eb', 'truck-2': '#9333ea', 'truck-3': '#0891b2' }
const riskLabel: Record<Schemas['RiskKind'], string> = { EMPTY: '빈 대여소 위험', OVERFLOW: '과적재 위험', NONE: '정상' }
const roleLabel: Record<Schemas['StationRole'], string> = { FIXED_PICKUP: '고정 회수', FIXED_DROPOFF: '고정 배치', VARIABLE: '변동' }

const stations = DUMMY_STATIONS
const plan = DUMMY_PLAN
const trucks = DUMMY_TRUCKS
const pending = ref([...DUMMY_PENDING])
const openCard = ref<string | null>(null)
const selected = ref<string | null>('ST-503')
const focusTruck = ref<string | null>(null)
const toast = ref('')
const question = ref('')
const answer = ref('')

const station = computed(() => stations.find((s) => s.stationId === selected.value))
const stopOf = computed(() => {
  for (const r of plan.routes) {
    const s = r.stops.find((x) => x.stationId === selected.value)
    if (s) return { ...s, truckId: r.truckId }
  }
  return null
})
const unservedOf = computed(() => plan.unserved.find((u) => u.stationId === selected.value))

const allStops = plan.routes.flatMap((r) => r.stops)
const kpis = computed(() => [
  { label: '빈 대여소 위험', value: stations.filter((s) => s.risk === 'EMPTY').length, unit: '곳', tone: 'empty' },
  { label: '과적재 위험', value: stations.filter((s) => s.risk === 'OVERFLOW').length, unit: '곳', tone: 'overflow' },
  { label: '진행 정류장', value: `${allStops.filter((s) => s.status === 'DONE').length}/${allStops.length}`, unit: '', tone: 'info' },
  { label: '대기 중 변경안', value: pending.value.length, unit: '건', tone: pending.value.length ? 'overflow' : 'ok' },
  { label: '무승인 변경', value: 0, unit: '건', tone: 'ok' },
])

function progress(r: Schemas['Route']) {
  return Math.round((r.stops.filter((s) => s.status === 'DONE').length / r.stops.length) * 100)
}

function notify(text: string) {
  toast.value = text
  window.setTimeout(() => (toast.value = ''), 2600)
}

// 더미: 결정을 보내지 않고 목록에서만 뺀다. 실제로는 POST /api/ai/tasks/{taskId}/approval
function decide(body: ApprovalDecision) {
  pending.value = pending.value.filter((p) => p.approvalId !== body.approvalId)
  openCard.value = null
  notify(body.decision === 'APPROVE' ? '승인했다 (더미)' : '거절했다 (더미)')
}

// 더미: 정해 둔 답을 보여 준다. 실제로는 QUESTION 작업을 만들고 SSE로 답을 받는다.
function ask() {
  if (!question.value.trim()) return
  answer.value =
    '오늘 성수·뚝섬 회수가 많은 이유는 서울숲 정문(3시간 뒤 34대)과 뚝섬유원지역(31대)이 거치대 수를 넘을 것으로 예측되기 때문이다. 두 곳 모두 고정 회수 거점이다.'
}
</script>

<template>
  <div class="page-head">
    <div>
      <h1>관제 대시보드 <span class="dummy-tag">더미 데이터</span></h1>
      <p>{{ DUMMY_REGION.name }} · 오전 근무조 07:00–10:00 · 계획 v{{ plan.version }} <span class="badge ACTIVE">활성</span></p>
    </div>
    <!-- 계획 초안·상황 반영 요청·질의는 모두 작업 콘솔에서 종류를 골라 만든다 -->
    <button @click="router.push('/console')">작업 요청</button>
  </div>

  <section class="kpis">
    <div v-for="k in kpis" :key="k.label" :class="['kpi', k.tone]">
      <span class="kpi-label">{{ k.label }}</span>
      <strong>{{ k.value }}<small>{{ k.unit }}</small></strong>
    </div>
  </section>

  <div class="grid">
    <section class="card map-card">
      <div class="map-head">
        <h2>위험 거점 지도</h2>
        <div class="legend">
          <span><i class="dot empty" />빈 대여소 위험</span>
          <span><i class="dot overflow" />과적재 위험</span>
          <span><i class="dot none" />정상</span>
          <span class="sep" />
          <button
            v-for="r in plan.routes"
            :key="r.truckId"
            :class="['chip', { off: focusTruck && focusTruck !== r.truckId }]"
            :style="{ '--c': colors[r.truckId] }"
            @click="focusTruck = focusTruck === r.truckId ? null : r.truckId"
          >
            {{ r.truckId }}
          </button>
        </div>
      </div>
      <StationMap
        :stations="stations"
        :routes="plan.routes"
        :trucks="trucks"
        :colors="colors"
        :selected="selected"
        :focus-truck="focusTruck"
        @select="selected = $event"
      />
      <p class="muted explain">💡 {{ plan.explanation }}</p>
    </section>

    <aside class="side">
      <section class="card">
        <h2>대기 중 변경안 <span class="count-pill">{{ pending.length }}</span></h2>
        <p v-if="!pending.length" class="muted">승인할 변경안이 없다</p>
        <div v-for="p in pending" :key="p.approvalId" class="pending">
          <div class="pending-row" @click="openCard = openCard === p.approvalId ? null : p.approvalId">
            <div>
              <span class="badge WAITING_APPROVAL">{{ kindLabel[p.kind] }}</span>
              <span v-if="p.grade === 'MAJOR'" class="badge FAILED">큰 변경</span>
              <p class="pending-title">{{ p.summary }}</p>
              <span class="hint">{{ p.createdMin }}분 전<template v-if="p.truckId"> · {{ p.truckId }}</template></span>
            </div>
            <span class="chev" :class="{ open: openCard === p.approvalId }">›</span>
          </div>
          <ApprovalCard v-if="openCard === p.approvalId" :card="p" @decide="decide" />
        </div>
      </section>

      <section v-if="station" class="card">
        <h2>{{ station.name }} <span class="muted">{{ station.stationId }}</span></h2>
        <div class="stock">
          <div class="stock-bar">
            <span :style="{ width: `${Math.min(100, (station.bikes / station.docks) * 100)}%` }" :class="station.risk" />
          </div>
          <span class="muted">재고 {{ station.bikes }} / 거치대 {{ station.docks }}</span>
        </div>
        <dl class="facts">
          <dt>3시간 뒤 예측</dt><dd>{{ station.forecastBikes }}대</dd>
          <dt>위험</dt><dd><span :class="['risk', station.risk]">{{ riskLabel[station.risk] }}</span></dd>
          <dt>역할</dt><dd>{{ station.role ? roleLabel[station.role] : '-' }}</dd>
          <dt>고도</dt><dd>{{ station.elevationM }}m</dd>
        </dl>
        <div v-if="stopOf" class="plan-note" :style="{ '--c': colors[stopOf.truckId] }">
          <strong>{{ stopOf.truckId }} · {{ stopOf.seq }}번째 · {{ actionLabel[stopOf.action] }} {{ stopOf.bikes }}대</strong>
          <span>{{ stopOf.reason }}</span>
        </div>
        <div v-else-if="unservedOf" class="plan-note dropped">
          <strong>포기 거점 · 우선순위 {{ unservedOf.priority.toFixed(2) }}</strong>
          <span>{{ reasonText(unservedOf.reason) }}</span>
        </div>
        <p v-else class="muted">이번 계획에 들어가지 않았다</p>
      </section>
    </aside>
  </div>

  <div class="grid lower">
    <section class="card">
      <h2>트럭별 경로</h2>
      <div v-for="r in plan.routes" :key="r.truckId" class="truck-row">
        <div class="truck-meta">
          <span class="truck-dot" :style="{ background: colors[r.truckId] }" />
          <div>
            <strong>{{ r.truckId }}</strong>
            <span class="hint">
              {{ trucks.find((t) => t.truckId === r.truckId)?.driver }} ·
              {{ trucks.find((t) => t.truckId === r.truckId)?.moving ? '운행 중' : '정차' }} ·
              {{ trucks.find((t) => t.truckId === r.truckId)?.updatedSec }}초 전
            </span>
          </div>
          <div class="progress"><span :style="{ width: `${progress(r)}%`, background: colors[r.truckId] }" /></div>
          <span class="hint">{{ progress(r) }}%</span>
        </div>
        <ol class="stops">
          <li
            v-for="s in r.stops"
            :key="s.seq"
            :class="['stop', s.action, s.status, { sel: s.stationId === selected }]"
            @click="selected = s.stationId"
          >
            <span class="seq">{{ s.seq }}</span>
            <span class="stop-name">{{ s.stationName }}</span>
            <span class="stop-act">
              {{ actionLabel[s.action] }} {{ s.bikes }}
              <span class="eta">{{ s.eta ? new Date(s.eta).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) : '' }}</span>
            </span>
          </li>
        </ol>
      </div>
    </section>

    <aside class="side">
      <section class="card">
        <h2>포기 거점 <span class="count-pill gray">{{ plan.unserved.length }}</span></h2>
        <ul class="dropped-list">
          <li v-for="u in plan.unserved" :key="u.stationId" @click="selected = u.stationId">
            <div>
              <strong>{{ u.stationName }}</strong>
              <span class="hint">{{ reasonText(u.reason) }}</span>
            </div>
            <span class="prio">{{ u.priority.toFixed(2) }}</span>
          </li>
        </ul>
      </section>

      <section class="card">
        <h2>에이전트에게 묻기</h2>
        <form class="ask" @submit.prevent="ask">
          <input v-model="question" placeholder="오늘 성수 쪽 회수가 왜 많지?" />
          <button>질의</button>
        </form>
        <p v-if="answer" class="answer-box">{{ answer }}<span class="hint">근거: 예측 재고 (forecast), 계획 v3</span></p>
      </section>
    </aside>
  </div>

  <Transition name="toast"><div v-if="toast" class="toast">{{ toast }}</div></Transition>
</template>

<style scoped>
.kpis { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; margin-bottom: 18px; }
.kpi { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px 16px; display: flex; flex-direction: column; gap: 6px; box-shadow: var(--shadow); position: relative; overflow: hidden; }
.kpi::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 4px; background: var(--ink-3); }
.kpi.empty::before { background: var(--empty); }
.kpi.overflow::before { background: var(--overflow); }
.kpi.info::before { background: var(--info); }
.kpi.ok::before { background: var(--ok); }
.kpi-label { font-size: 12px; color: var(--ink-3); font-weight: 600; }
.kpi strong { font-size: 26px; letter-spacing: -0.02em; }
.kpi small { font-size: 13px; color: var(--ink-3); margin-left: 2px; font-weight: 600; }

.grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 18px; align-items: start; }
.grid.lower { margin-top: 18px; }
.side { display: flex; flex-direction: column; gap: 18px; }
.map-card { padding: 16px; }
.map-head { display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 8px; margin-bottom: 10px; }
.map-head h2 { margin: 0; }
.legend { display: flex; align-items: center; gap: 12px; font-size: 12px; color: var(--ink-2); flex-wrap: wrap; }
.legend span { display: inline-flex; align-items: center; gap: 5px; }
.dot { width: 10px; height: 10px; border-radius: 50%; display: inline-block; }
.dot.empty { background: var(--empty); }
.dot.overflow { background: var(--overflow); }
.dot.none { background: #94a3b8; }
.sep { width: 1px; height: 16px; background: var(--line); }
.chip { padding: 3px 10px; font-size: 12px; border-radius: 999px; background: var(--c); color: #fff; }
.chip:hover:not(:disabled) { background: var(--c); filter: brightness(0.92); }
.chip.off { background: #fff; color: var(--c); box-shadow: inset 0 0 0 1px var(--c); }
.explain { margin: 10px 4px 0; }

.count-pill { background: var(--overflow); color: #fff; border-radius: 999px; font-size: 12px; padding: 1px 8px; }
.count-pill.gray { background: var(--ink-3); }
.pending { border: 1px solid var(--line); border-radius: 12px; margin-bottom: 8px; overflow: hidden; }
.pending-row { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px; cursor: pointer; }
.pending-row:hover { background: #f8fafc; }
.pending-title { margin: 6px 0 2px; font-size: 14px; font-weight: 600; line-height: 1.4; }
.chev { font-size: 22px; color: var(--ink-3); transition: transform 0.15s; }
.chev.open { transform: rotate(90deg); }
.pending :deep(.approval) { margin: 0; border-radius: 0; border-width: 2px 0 0; }

.stock { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.stock-bar { height: 10px; background: #eef2f6; border-radius: 999px; overflow: hidden; }
.stock-bar span { display: block; height: 100%; background: #94a3b8; border-radius: 999px; }
.stock-bar span.EMPTY { background: var(--empty); }
.stock-bar span.OVERFLOW { background: var(--overflow); }
.facts { display: grid; grid-template-columns: auto 1fr; gap: 6px 14px; margin: 0 0 12px; font-size: 13px; }
.facts dt { color: var(--ink-3); }
.facts dd { margin: 0; font-weight: 600; }
.risk.EMPTY { color: var(--empty); }
.risk.OVERFLOW { color: #b45309; }
.plan-note { border-left: 4px solid var(--c); background: #f8fafc; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.plan-note.dropped { --c: var(--ink-3); }

.truck-row { padding: 12px 0; border-top: 1px solid #f1f5f9; }
.truck-row:first-of-type { border-top: 0; padding-top: 0; }
.truck-meta { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
.truck-meta > div:first-of-type { display: flex; flex-direction: column; min-width: 180px; }
.truck-dot { width: 12px; height: 12px; border-radius: 4px; }
.progress { flex: 1; height: 6px; background: #eef2f6; border-radius: 999px; overflow: hidden; }
.progress span { display: block; height: 100%; border-radius: 999px; }
.stops { list-style: none; display: flex; gap: 8px; padding: 0; margin: 0; overflow-x: auto; padding-bottom: 4px; }
.stop { flex: 0 0 auto; display: grid; grid-template-columns: auto 1fr; grid-template-areas: 'seq name' 'seq act'; gap: 0 8px; padding: 8px 12px 8px 8px; border: 1px solid var(--line); border-radius: 10px; font-size: 12px; cursor: pointer; background: #fff; }
.stop:hover { border-color: var(--line-2); }
.stop.sel { border-color: var(--ink); }
.stop.DONE { background: #f8fafc; color: var(--ink-3); }
.seq { grid-area: seq; width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; background: #eef2f6; align-self: center; }
.stop.PICKUP .seq { background: #e0f2fe; color: #0369a1; }
.stop.DROPOFF .seq { background: #dcfce7; color: #15803d; }
.stop.DONE .seq { background: #e2e8f0; color: var(--ink-3); }
.stop-name { grid-area: name; font-weight: 600; }
.stop-act { grid-area: act; }
.eta { color: var(--ink-3); margin-left: 4px; font-size: 11px; }

.dropped-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.dropped-list li { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 12px; border: 1px dashed var(--line-2); border-radius: 10px; cursor: pointer; }
.dropped-list li div { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.prio { font-weight: 700; font-size: 13px; color: var(--ink-2); background: #eef2f6; padding: 2px 8px; border-radius: 6px; }
.ask { flex-direction: row; margin: 0; }
.ask input { flex: 1; }
.answer-box { margin: 12px 0 0; padding: 12px; background: var(--brand-soft); border-radius: 10px; font-size: 13px; line-height: 1.6; display: flex; flex-direction: column; gap: 6px; }

.toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%); background: var(--ink); color: #fff; padding: 10px 18px; border-radius: 999px; font-size: 14px; box-shadow: var(--shadow); }
.toast-enter-active, .toast-leave-active { transition: all 0.2s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translate(-50%, 8px); }

@media (max-width: 1100px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
  .kpis { grid-template-columns: repeat(3, 1fr); }
}
</style>
