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
// 사람 승인 없이 적용된 변경 수. 늘 0이어야 하는 안전 지표다. 더미: 실제로는 감사 로그에서 센다.
const unapprovedChanges = 0
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
const truckOf = (id: string) => trucks.find((t) => t.truckId === id)

const allStops = plan.routes.flatMap((r) => r.stops)
const doneStops = allStops.filter((s) => s.status === 'DONE').length
const kpis = computed(() => [
  { key: 'empty', label: '빈 대여소 위험', value: stations.filter((s) => s.risk === 'EMPTY').length, unit: '곳', sub: '3시간 뒤 예측 기준' },
  { key: 'overflow', label: '과적재 위험', value: stations.filter((s) => s.risk === 'OVERFLOW').length, unit: '곳', sub: '거치대 수 초과 예상' },
  { key: 'info', label: '정류장 진행', value: `${doneStops}/${allStops.length}`, unit: '', sub: `트럭 ${plan.routes.length}대 운행`, ratio: doneStops / allStops.length },
  {
    key: pending.value.length ? 'pending' : 'ok',
    label: '승인 대기',
    value: pending.value.length,
    unit: '건',
    sub: pending.value.length ? `가장 오래된 건 ${Math.max(...pending.value.map((p) => p.createdMin))}분 전` : '모두 처리했다',
  },
])
// KPI 아이콘 (24×24 선 그림)
const kpiIcon: Record<string, string> = {
  empty: 'M12 3v4M12 17v4M5 12H3M21 12h-2M7 7 5.5 5.5M18.5 18.5 17 17M7 17l-1.5 1.5M18.5 5.5 17 7M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  overflow: 'M4 20h16M6 16V9M10 16V5M14 16V8M18 16V11',
  info: 'M3 17h2l3-8 4 10 3-6 2 4h4',
  pending: 'M12 7v5l3 2M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18z',
  ok: 'M5 12.5 10 17 19 7',
}

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
  <header class="hero">
    <div class="hero-text">
      <p class="eyebrow">{{ DUMMY_REGION.name }} · 오전 근무조 07:00–10:00</p>
      <h1>관제 대시보드 <span class="dummy-tag">더미 데이터</span></h1>
      <div class="hero-meta">
        <span class="badge ACTIVE">계획 v{{ plan.version }} 활성</span>
        <span v-if="unapprovedChanges === 0" class="safety">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>
          모든 변경은 사람이 승인했다
        </span>
        <span v-else class="safety bad">⚠ 승인 없이 바뀐 변경 {{ unapprovedChanges }}건</span>
      </div>
    </div>
    <div class="hero-actions">
      <!-- 계획 초안·상황 반영 요청은 작업 콘솔에서 종류를 골라 만든다 -->
      <button class="cta" @click="router.push('/console')">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
        작업 요청
      </button>
      <span class="hint">계획 만들기 · 현장 상황 반영</span>
    </div>
    <!-- 로그인 화면과 이어지는 도시 실루엣 (장식) -->
    <svg class="skyline" viewBox="0 0 420 120" preserveAspectRatio="xMaxYMax meet" aria-hidden="true">
      <g fill="#fff" opacity="0.75">
        <rect x="20" y="62" width="34" height="58" rx="3" /><rect x="60" y="40" width="28" height="80" rx="3" />
        <rect x="94" y="70" width="40" height="50" rx="3" /><rect x="140" y="22" width="36" height="98" rx="3" />
        <rect x="182" y="56" width="30" height="64" rx="3" /><rect x="218" y="80" width="46" height="40" rx="3" />
        <rect x="270" y="46" width="32" height="74" rx="3" /><rect x="308" y="66" width="38" height="54" rx="3" />
        <rect x="352" y="34" width="30" height="86" rx="3" /><rect x="388" y="74" width="32" height="46" rx="3" />
      </g>
      <rect x="140" y="22" width="36" height="6" rx="2" fill="#0f9d58" opacity="0.8" />
      <line x1="158" y1="22" x2="158" y2="8" stroke="#0f9d58" stroke-width="2" opacity="0.8" />
      <circle cx="158" cy="7" r="3" fill="#0f9d58" />
    </svg>
  </header>

  <section class="kpis">
    <div v-for="k in kpis" :key="k.label" :class="['kpi', k.key]">
      <span class="kpi-icon" aria-hidden="true">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path :d="kpiIcon[k.key]" /></svg>
      </span>
      <div class="kpi-body">
        <span class="kpi-label">{{ k.label }}</span>
        <strong>{{ k.value }}<small>{{ k.unit }}</small></strong>
        <div v-if="k.ratio !== undefined" class="kpi-bar"><span :style="{ width: `${Math.round(k.ratio * 100)}%` }" /></div>
        <span class="kpi-sub">{{ k.sub }}</span>
      </div>
    </div>
  </section>

  <div class="grid">
    <section class="card map-card">
      <div class="map-head">
        <div>
          <h2>위험 거점 지도</h2>
          <p class="hint">대여소를 누르면 오른쪽에 상세가 나온다 · 실선은 지난 구간, 점선은 남은 구간</p>
        </div>
        <div class="trucks-filter" role="group" aria-label="트럭 경로 골라 보기">
          <button :class="['chip', 'all', { on: !focusTruck }]" :aria-pressed="!focusTruck" @click="focusTruck = null">전체</button>
          <button
            v-for="r in plan.routes"
            :key="r.truckId"
            :class="['chip', { on: focusTruck === r.truckId }]"
            :style="{ '--c': colors[r.truckId] }"
            :aria-pressed="focusTruck === r.truckId"
            @click="focusTruck = focusTruck === r.truckId ? null : r.truckId"
          >
            <i />{{ r.truckId.replace('truck-', 'T') }} {{ truckOf(r.truckId)?.driver }}
          </button>
        </div>
      </div>
      <div class="ai-summary">
        <span class="ai-mark" aria-hidden="true">AI</span>
        <div><strong>계획 요약</strong><span>{{ plan.explanation }}</span></div>
      </div>
      <div class="map-wrap">
        <StationMap
          :stations="stations"
          :routes="plan.routes"
          :trucks="trucks"
          :colors="colors"
          :selected="selected"
          :focus-truck="focusTruck"
          @select="selected = $event"
        />
        <ul class="legend" aria-label="대여소 색">
          <li><i class="dot empty" />빈 대여소 위험</li>
          <li><i class="dot overflow" />과적재 위험</li>
          <li><i class="dot none" />정상</li>
          <li class="muted-li">원 크기 = 거치대 수</li>
        </ul>
      </div>
    </section>

    <aside class="side">
      <section class="card">
        <h2>승인 대기 <span :class="['count-pill', { zero: !pending.length }]">{{ pending.length }}</span></h2>
        <p v-if="!pending.length" class="empty-note">승인할 변경안이 없다</p>
        <div v-for="p in pending" :key="p.approvalId" :class="['pending', { major: p.grade === 'MAJOR', open: openCard === p.approvalId }]">
          <button class="pending-row" :aria-expanded="openCard === p.approvalId" @click="openCard = openCard === p.approvalId ? null : p.approvalId">
            <div>
              <span class="badges">
                <span class="badge WAITING_APPROVAL">{{ kindLabel[p.kind] }}</span>
                <span v-if="p.grade === 'MAJOR'" class="badge FAILED">큰 변경</span>
              </span>
              <span class="pending-title">{{ p.summary }}</span>
              <span class="hint">{{ p.createdMin }}분 전<template v-if="p.truckId"> · {{ p.truckId }}</template></span>
            </div>
            <span class="chev" aria-hidden="true">›</span>
          </button>
          <ApprovalCard v-if="openCard === p.approvalId" :card="p" @decide="decide" />
        </div>
      </section>

      <section v-if="station" class="card">
        <p class="eyebrow">선택한 대여소</p>
        <h2>{{ station.name }} <span class="muted">{{ station.stationId }}</span></h2>
        <div class="stock">
          <div class="stock-bar">
            <span :style="{ width: `${Math.min(100, (station.bikes / station.docks) * 100)}%` }" :class="station.risk" />
          </div>
          <span class="muted">재고 <strong>{{ station.bikes }}</strong> / 거치대 {{ station.docks }}</span>
        </div>
        <dl class="facts">
          <div><dt>3시간 뒤 예측</dt><dd>{{ station.forecastBikes }}대</dd></div>
          <div><dt>위험</dt><dd><span :class="['risk', station.risk]">{{ riskLabel[station.risk] }}</span></dd></div>
          <div><dt>역할</dt><dd>{{ station.role ? roleLabel[station.role] : '-' }}</dd></div>
          <div><dt>고도</dt><dd>{{ station.elevationM }}m</dd></div>
        </dl>
        <div v-if="stopOf" class="plan-note" :style="{ '--c': colors[stopOf.truckId] }">
          <strong>{{ stopOf.truckId }} · {{ stopOf.seq }}번째 · {{ actionLabel[stopOf.action] }} {{ stopOf.bikes }}대</strong>
          <span>{{ stopOf.reason }}</span>
        </div>
        <div v-else-if="unservedOf" class="plan-note dropped">
          <strong>이번 계획에서 빠짐 · 우선순위 {{ unservedOf.priority.toFixed(2) }}</strong>
          <span>{{ reasonText(unservedOf.reason) }}</span>
        </div>
        <p v-else class="muted">이번 계획에 들어가지 않았다</p>
      </section>
    </aside>
  </div>

  <div class="grid lower">
    <section class="card">
      <h2>트럭별 경로</h2>
      <div v-for="r in plan.routes" :key="r.truckId" class="truck-row" :style="{ '--c': colors[r.truckId] }">
        <div class="truck-meta">
          <span class="truck-dot">{{ r.truckId.replace('truck-', 'T') }}</span>
          <div class="truck-name">
            <strong>{{ r.truckId }} · {{ truckOf(r.truckId)?.driver }}</strong>
            <span class="hint">
              <i :class="['live', { moving: truckOf(r.truckId)?.moving }]" />
              {{ truckOf(r.truckId)?.moving ? '운행 중' : '정차' }} · 적재 {{ truckOf(r.truckId)?.load }}/{{ truckOf(r.truckId)?.capacity }} ·
              {{ truckOf(r.truckId)?.updatedSec }}초 전
            </span>
          </div>
          <div class="progress"><span :style="{ width: `${progress(r)}%` }" /></div>
          <span class="pct">{{ progress(r) }}%</span>
        </div>
        <ol class="stops">
          <li
            v-for="s in r.stops"
            :key="s.seq"
            :class="['stop', s.action, s.status, { sel: s.stationId === selected }]"
            tabindex="0"
            @click="selected = s.stationId"
            @keydown.enter="selected = s.stationId"
          >
            <span class="seq">{{ s.status === 'DONE' ? '✓' : s.seq }}</span>
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
        <h2>이번 계획에서 빠진 대여소 <span class="count-pill gray">{{ plan.unserved.length }}</span></h2>
        <p class="hint section-hint">트럭·시간이 모자라 이번엔 들르지 못하는 곳. 숫자는 우선순위(높을수록 급함)</p>
        <ul class="dropped-list">
          <li v-for="u in plan.unserved" :key="u.stationId" tabindex="0" @click="selected = u.stationId" @keydown.enter="selected = u.stationId">
            <div>
              <strong>{{ u.stationName }}</strong>
              <span class="hint">{{ reasonText(u.reason) }}</span>
            </div>
            <span class="prio">{{ u.priority.toFixed(2) }}</span>
          </li>
        </ul>
      </section>

      <section class="card">
        <h2>이 화면에 대해 묻기</h2>
        <p class="hint section-hint">지금 계획과 지도에 대한 빠른 질문. 계획을 새로 만들거나 상황을 반영하려면 위의 <strong>작업 요청</strong>을 쓴다.</p>
        <form class="ask" @submit.prevent="ask">
          <input v-model="question" placeholder="오늘 성수 쪽 회수가 왜 많지?" aria-label="에이전트에게 물을 내용" />
          <button>묻기</button>
        </form>
        <p v-if="answer" class="answer-box">{{ answer }}<span class="hint">근거: 예측 재고 (forecast), 계획 v{{ plan.version }}</span></p>
      </section>
    </aside>
  </div>

  <Transition name="toast"><div v-if="toast" class="toast" role="status">{{ toast }}</div></Transition>
</template>

<style scoped>
/* 머리: 로그인 화면의 하늘색 + 도시 실루엣 */
.hero { position: relative; overflow: hidden; display: flex; justify-content: space-between; align-items: flex-end; gap: 16px; flex-wrap: wrap; padding: 24px 28px; margin-bottom: 18px; border-radius: 18px; background: var(--sky); border: 1px solid #d3e7f3; }
.hero-text, .hero-actions { position: relative; z-index: 1; }
.hero h1 { font-size: 26px; margin: 0 0 10px; display: flex; align-items: center; gap: 8px; }
.eyebrow { margin: 0 0 4px; font-size: 12px; font-weight: 700; color: var(--brand-strong); letter-spacing: 0.02em; }
.hero-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.hero-meta .badge { margin: 0; }
.safety { display: inline-flex; align-items: center; gap: 5px; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 700; color: #166534; background: rgb(255 255 255 / 0.8); border: 1px solid #bbf7d0; }
.safety.bad { color: #991b1b; background: var(--empty-soft); border-color: #fecaca; }
.hero-actions { display: flex; flex-direction: column; align-items: flex-end; gap: 6px; margin-right: 0; }
.cta { display: inline-flex; align-items: center; gap: 6px; padding: 11px 20px; font-size: 15px; box-shadow: 0 6px 16px rgb(15 157 88 / 0.28); }
.skyline { position: absolute; right: 200px; bottom: 0; height: 100%; width: 420px; pointer-events: none; }

/* KPI */
.kpis { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 18px; }
.kpi { --k: var(--ink-3); --k-soft: #eef2f6; display: flex; gap: 12px; align-items: flex-start; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 16px; box-shadow: var(--shadow); }
.kpi.empty { --k: var(--empty); --k-soft: var(--empty-soft); }
.kpi.overflow, .kpi.pending { --k: #d97706; --k-soft: var(--overflow-soft); }
.kpi.info { --k: var(--info); --k-soft: var(--info-soft); }
.kpi.ok { --k: var(--brand); --k-soft: var(--brand-soft); }
.kpi-icon { flex: none; width: 38px; height: 38px; border-radius: 11px; display: grid; place-items: center; color: var(--k); background: var(--k-soft); }
.kpi-body { display: flex; flex-direction: column; gap: 2px; min-width: 0; flex: 1; }
.kpi-label { font-size: 12px; color: var(--ink-3); font-weight: 600; }
.kpi strong { font-size: 26px; letter-spacing: -0.02em; line-height: 1.2; }
.kpi small { font-size: 13px; color: var(--ink-3); margin-left: 2px; font-weight: 600; }
.kpi-sub { font-size: 12px; color: var(--ink-3); }
.kpi-bar { height: 5px; background: #eef2f6; border-radius: 999px; overflow: hidden; margin: 4px 0 2px; }
.kpi-bar span { display: block; height: 100%; background: var(--k); border-radius: 999px; }

.grid { display: grid; grid-template-columns: minmax(0, 1fr) 360px; gap: 18px; align-items: start; }
.grid.lower { margin-top: 18px; }
.side { display: flex; flex-direction: column; gap: 18px; }

/* 지도 */
.map-card { padding: 18px; }
.map-head { display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 10px; margin-bottom: 12px; }
.map-head h2 { margin: 0 0 2px; }
.trucks-filter { display: flex; gap: 6px; flex-wrap: wrap; }
.chip { --c: var(--brand); display: inline-flex; align-items: center; gap: 6px; padding: 5px 11px; font-size: 12px; font-weight: 600; border-radius: 999px; background: #fff; color: var(--ink-2); border: 1px solid var(--line-2); }
.chip i { width: 8px; height: 8px; border-radius: 50%; background: var(--c); }
.chip:hover:not(:disabled) { background: #f8fafc; }
.chip.on { background: var(--c); border-color: var(--c); color: #fff; }
.chip.on i { background: #fff; }
.chip.on:hover:not(:disabled) { background: var(--c); filter: brightness(0.95); }
.ai-summary { display: flex; gap: 10px; align-items: flex-start; padding: 10px 12px; margin-bottom: 12px; border-radius: 12px; background: var(--brand-soft); font-size: 13px; color: var(--ink-2); line-height: 1.5; }
.ai-summary div { display: flex; flex-direction: column; }
.ai-summary strong { color: var(--brand-strong); font-size: 12px; }
.ai-mark { flex: none; width: 26px; height: 26px; border-radius: 8px; background: var(--brand); color: #fff; font-size: 11px; font-weight: 800; display: grid; place-items: center; }
.map-wrap { position: relative; }
.legend { position: absolute; left: 12px; bottom: 12px; display: flex; gap: 12px; flex-wrap: wrap; margin: 0; padding: 6px 12px; list-style: none; font-size: 12px; font-weight: 600; color: var(--ink-2); background: rgb(255 255 255 / 0.9); border-radius: 999px; box-shadow: var(--shadow); }
.legend li { display: inline-flex; align-items: center; gap: 5px; }
.legend .muted-li { color: var(--ink-3); font-weight: 500; }
.dot { width: 9px; height: 9px; border-radius: 50%; display: inline-block; }
.dot.empty { background: var(--empty); }
.dot.overflow { background: var(--overflow); }
.dot.none { background: var(--ok); }

/* 승인 대기 */
.count-pill { background: var(--overflow); color: #fff; border-radius: 999px; font-size: 12px; padding: 1px 8px; }
.count-pill.zero { background: var(--ok); }
.count-pill.gray { background: var(--ink-3); }
.empty-note { margin: 0; padding: 14px; text-align: center; font-size: 13px; color: var(--ink-3); background: #f8fafc; border-radius: 10px; }
.pending { border: 1px solid var(--line); border-left: 4px solid var(--overflow); border-radius: 12px; margin-bottom: 8px; overflow: hidden; }
.pending.major { border-left-color: var(--empty); }
.pending-row { width: 100%; display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 12px; text-align: left; font-weight: inherit; color: inherit; background: #fff; border-radius: 0; }
.pending-row:hover:not(:disabled) { background: #f8fafc; }
.pending-row > div { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; }
.badges { display: flex; flex-wrap: wrap; }
.pending-title { margin: 4px 0 0; font-size: 14px; font-weight: 600; line-height: 1.4; }
.chev { font-size: 22px; color: var(--ink-3); transition: transform 0.15s; }
.pending.open .chev { transform: rotate(90deg); }
.pending :deep(.approval) { margin: 0; border-radius: 0; border-width: 2px 0 0; }

/* 선택한 대여소 */
.stock { display: flex; flex-direction: column; gap: 6px; margin-bottom: 12px; }
.stock-bar { height: 10px; background: #eef2f6; border-radius: 999px; overflow: hidden; }
.stock-bar span { display: block; height: 100%; background: var(--ok); border-radius: 999px; }
.stock-bar span.EMPTY { background: var(--empty); }
.stock-bar span.OVERFLOW { background: var(--overflow); }
.facts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 0 0 12px; }
.facts div { padding: 8px 10px; background: #f8fafc; border-radius: 10px; }
.facts dt { color: var(--ink-3); font-size: 11px; font-weight: 600; }
.facts dd { margin: 2px 0 0; font-weight: 700; font-size: 14px; }
.risk.NONE { color: var(--brand-strong); }
.risk.EMPTY { color: var(--empty); }
.risk.OVERFLOW { color: #b45309; }
.plan-note { border-left: 4px solid var(--c); background: #f8fafc; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.plan-note.dropped { --c: var(--ink-3); }

/* 트럭별 경로 */
.truck-row { padding: 14px 0; border-top: 1px solid #f1f5f9; }
.truck-row:first-of-type { border-top: 0; padding-top: 0; }
.truck-meta { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
.truck-dot { flex: none; width: 34px; height: 34px; border-radius: 10px; background: var(--c); color: #fff; font-size: 12px; font-weight: 800; display: grid; place-items: center; }
.truck-name { display: flex; flex-direction: column; min-width: 220px; }
.live { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--ink-3); margin-right: 2px; vertical-align: 1px; }
.live.moving { background: var(--ok); box-shadow: 0 0 0 3px rgb(16 185 129 / 0.2); }
.progress { flex: 1; height: 6px; background: #eef2f6; border-radius: 999px; overflow: hidden; }
.progress span { display: block; height: 100%; border-radius: 999px; background: var(--c); }
.pct { font-size: 13px; font-weight: 700; color: var(--ink-2); min-width: 40px; text-align: right; }
.stops { list-style: none; display: flex; gap: 8px; padding: 0 0 4px; margin: 0; overflow-x: auto; }
.stop { flex: 0 0 auto; display: grid; grid-template-columns: auto 1fr; grid-template-areas: 'seq name' 'seq act'; gap: 0 8px; padding: 8px 12px 8px 8px; border: 1px solid var(--line); border-radius: 10px; font-size: 12px; cursor: pointer; background: #fff; }
.stop:hover { border-color: var(--line-2); }
.stop.sel { border-color: var(--c); box-shadow: 0 0 0 2px color-mix(in srgb, var(--c) 20%, transparent); }
.stop.DONE { background: #f8fafc; color: var(--ink-3); }
.seq { grid-area: seq; width: 22px; height: 22px; border-radius: 50%; display: grid; place-items: center; font-weight: 700; background: #eef2f6; align-self: center; }
.stop.PICKUP .seq { background: #e0f2fe; color: #0369a1; }
.stop.DROPOFF .seq { background: #dcfce7; color: #15803d; }
.stop.DONE .seq { background: #e2e8f0; color: var(--ink-3); }
.stop-name { grid-area: name; font-weight: 600; }
.stop-act { grid-area: act; }
.eta { color: var(--ink-3); margin-left: 4px; font-size: 11px; }

/* 빠진 대여소·묻기 */
.section-hint { display: block; margin: -4px 0 12px; line-height: 1.5; }
.dropped-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.dropped-list li { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 10px 12px; border: 1px dashed var(--line-2); border-radius: 10px; cursor: pointer; }
.dropped-list li:hover { background: #f8fafc; }
.dropped-list li div { display: flex; flex-direction: column; gap: 2px; font-size: 13px; }
.prio { font-weight: 700; font-size: 13px; color: var(--ink-2); background: #eef2f6; padding: 2px 8px; border-radius: 6px; }
.ask { flex-direction: row; margin: 0; }
.ask input { flex: 1; min-width: 0; }
.answer-box { margin: 12px 0 0; padding: 12px; background: var(--brand-soft); border-radius: 10px; font-size: 13px; line-height: 1.6; display: flex; flex-direction: column; gap: 6px; }

.toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%); background: var(--ink); color: #fff; padding: 10px 18px; border-radius: 999px; font-size: 14px; box-shadow: var(--shadow); }
.toast-enter-active, .toast-leave-active { transition: all 0.2s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translate(-50%, 8px); }

@media (max-width: 1100px) {
  .grid { grid-template-columns: minmax(0, 1fr); }
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .skyline { right: 0; opacity: 0.6; }
}
@media (max-width: 640px) {
  .hero { padding: 18px; }
  .hero-actions { align-items: flex-start; }
  .truck-name { min-width: 0; }
  .legend { position: static; margin-top: 8px; border-radius: 12px; }
}
</style>
