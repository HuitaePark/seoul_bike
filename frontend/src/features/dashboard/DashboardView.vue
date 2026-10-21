<!--
관제 대시보드 (PRD 10장, FR-01~09).
실시간: 승인 대기 카드·묻기 답·결정 결과는 근무조 작업 목록과 작업 SSE로 받는다 (useShiftFeed).
아직 더미: 대여소 현황(GET /api/regions/{regionId}/stations는 backend에 없다)과 트럭 위치(계약에 없다).
계획(GET /api/shifts/{shiftId}/plan)은 대여소 좌표가 있어야 지도에 그릴 수 있어 대여소 API와 같이 붙인다.
구성: 왼쪽 근무조 레일 · 맨 위 묻기 줄 · 3D 관제 뷰(+재고 현황) · 예측/진행률/트럭 적재/대여소/에이전트 대화 · 트럭별 경로.
계획 만들기·상황 반영 요청은 상단 메뉴의 작업 콘솔에서 한다 (같은 곳으로 가는 버튼을 따로 두지 않는다).
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { Schemas } from '../../shared/api/client'
import { DUMMY_PLAN, DUMMY_REGION, DUMMY_STATIONS, DUMMY_TRUCKS } from '../../shared/dummy'
import { makeShiftId, SHIFT_SLOTS, slotAt, todayInSeoul, type ShiftSlot } from '../../shared/region/regions'
import type { ApprovalDecision } from '../approval/approval'
import { actionLabel } from '../approval/approval'
import { reasonText } from '../plan-map/model'
import AgentChat from './AgentChat.vue'
import AskBar from './AskBar.vue'
import ForecastChart from './ForecastChart.vue'
import IsoStationView from './IsoStationView.vue'
import RingGauge from './RingGauge.vue'
import StationMap from './StationMap.vue'
import TruckLoad from './TruckLoad.vue'
import { riskShades } from './iso'
import type { LiveCard } from './feed'
import type { RoutePreview } from './route-view'
import { legOf, previewOf } from './route-view'
import { useShiftFeed } from './useShiftFeed'

const colors: Record<string, string> = { 'truck-1': '#2563eb', 'truck-2': '#9333ea', 'truck-3': '#0891b2' }
const riskLabel: Record<Schemas['RiskKind'], string> = { EMPTY: '빈 대여소 위험', OVERFLOW: '과적재 위험', NONE: '정상' }
const roleLabel: Record<Schemas['StationRole'], string> = { FIXED_PICKUP: '고정 회수', FIXED_DROPOFF: '고정 배치', VARIABLE: '변동' }

const stations = DUMMY_STATIONS
const plan = DUMMY_PLAN
const trucks = DUMMY_TRUCKS
// 오늘(서울 날짜) 근무조. 시간대는 지금 하고 있거나 다음에 올 것으로 시작하고 레일에서 바꾼다.
const slot = ref<ShiftSlot>(slotAt())
const shiftId = computed(() => makeShiftId(DUMMY_REGION.id, todayInSeoul(), slot.value))
const feed = useShiftFeed(shiftId)
const { pending, log, qa, status: feedStatus, busy, errors } = feed
const feedLabel = { loading: '연결 중', live: '실시간', offline: '연결 끊김 · 다시 시도 중' } as const
// 사람 승인 없이 적용된 변경 수. 늘 0이어야 하는 안전 지표다. 더미: 실제로는 감사 로그에서 센다.
const unapprovedChanges = 0
const view = ref<'3d' | 'map'>('3d')
const selected = ref<string | null>('ST-503')
const focusTruck = ref<string | null>(null)
const loadTruck = ref('truck-1')
const toast = ref('')
// 승인 대기 변경안을 지도에 겹쳐 본다. 켜져 있으면 그 변경안의 트럭·대여소만 밝게 보인다.
const preview = ref<RoutePreview | null>(null)
const openDropped = ref<string | null>(null)

// 지도에서 트럭 하나만 보면 적재 카드도 그 트럭을 보여 준다
watch(focusTruck, (t) => {
  if (t) loadTruck.value = t
})

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
const stationById = new Map(stations.map((s) => [s.stationId, s]))
const stationOf = (id: string) => stationById.get(id)

const allStops = plan.routes.flatMap((r) => r.stops.map((s) => ({ ...s, truckId: r.truckId })))
const doneStops = allStops.filter((s) => s.status === 'DONE').length
const movedBikes = allStops.filter((s) => s.action === 'PICKUP').reduce((n, s) => n + s.bikes, 0)
const timeOf = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) : '')
const nextStop = allStops.filter((s) => s.status !== 'DONE').sort((a, b) => (a.eta ?? '').localeCompare(b.eta ?? ''))[0]

// 위험 순: 빈 대여소(재고 적은 순) → 과적재(넘치는 순)
const riskOrder = [...stations]
  .filter((s) => s.risk !== 'NONE')
  .sort((a, b) => (a.risk === b.risk ? (a.risk === 'EMPTY' ? a.bikes - b.bikes : (b.forecastBikes ?? b.bikes) - b.docks - ((a.forecastBikes ?? a.bikes) - a.docks)) : a.risk === 'EMPTY' ? -1 : 1))
const riskCounts = (['EMPTY', 'OVERFLOW', 'NONE'] as const).map((k) => ({ k, n: stations.filter((s) => s.risk === k).length }))

const routeOf = (id: string) => plan.routes.find((r) => r.truckId === id)!
function progress(r: Schemas['Route']) {
  return Math.round((r.stops.filter((s) => s.status === 'DONE').length / r.stops.length) * 100)
}
// 트럭이 다음 정류장까지 얼마나 왔는지 (타임라인 선을 그만큼 더 잇는다)
const legs = Object.fromEntries(plan.routes.map((r) => [r.truckId, legOf(r, truckOf(r.truckId), stationById)]))
const loadInfo = computed(() => {
  const t = truckOf(loadTruck.value)!
  const r = routeOf(loadTruck.value)
  // 지도 진행선(legOf)과 같은 기준: 남은 정류장은 PENDING만. 건너뛴(SKIPPED) 곳은 다음 정류장이 아니다.
  const left = r.stops.filter((s) => (s.status ?? 'PENDING') === 'PENDING')
  return { t, next: left[0], left: left.length }
})

const nav = [
  { id: 'view', label: '관제 뷰', icon: 'M3 7l9-4 9 4-9 4-9-4zm0 5 9 4 9-4M3 17l9 4 9-4' },
  { id: 'trend', label: '재고 예측', icon: 'M4 20V10M10 20V4M16 20v-7M22 20H2' },
  { id: 'truck', label: '트럭 적재', icon: 'M3 7h11v9H3zM14 10h4l3 3v3h-7M7 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zm10 0a2 2 0 1 0 0-4 2 2 0 0 0 0 4z' },
  { id: 'agent', label: '에이전트 대화', icon: 'M4 5h16v11H8l-4 4V5z' },
  { id: 'routes', label: '트럭별 경로', icon: 'M5 19a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM19 9a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM7 17h6a4 4 0 0 0 0-8h-2a4 4 0 0 1 0-8h6' },
  { id: 'dropped', label: '빠진 대여소', icon: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18zM5.6 5.6l12.8 12.8' },
]
const active = ref('view')
function go(id: string) {
  active.value = id
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

function pickTruck(id: string | null) {
  preview.value = null
  focusTruck.value = id
}

function showPreview(card: LiveCard | null, scroll: boolean) {
  preview.value = card ? previewOf(card, stations) : null
  if (card && scroll) go('view')
}

function openDrop(id: string) {
  selected.value = id
  openDropped.value = openDropped.value === id ? null : id
}

// QUESTION 작업을 만들고 답을 SSE로 받는다 (답은 묻기 줄과 에이전트 대화에 함께 보인다)
function ask(q: string) {
  void feed.ask(q)
}

function notify(text: string) {
  toast.value = text
  window.setTimeout(() => (toast.value = ''), 2600)
}

// POST /api/ai/tasks/{taskId}/approval. 받아들여지면 카드가 내려가고, 이어지는 결과는 에이전트 대화에 SSE로 붙는다.
async function decide(body: ApprovalDecision, card: LiveCard) {
  if (!(await feed.decide(card, body))) return
  if (preview.value?.approvalId === body.approvalId) preview.value = null
  notify(body.decision === 'APPROVE' ? '승인을 보냈다' : '거절을 보냈다')
}
</script>

<template>
  <div class="dash">
    <!-- 왼쪽 레일: 근무조 · 바로 가기 · 요약 -->
    <aside class="rail">
      <section class="rail-card shift">
        <p class="eyebrow">오늘 근무조</p>
        <strong class="region">{{ DUMMY_REGION.name }}</strong>
        <label class="slot">
          <span class="sr-only">시간대</span>
          <select v-model="slot" :title="shiftId">
            <option v-for="s in SHIFT_SLOTS" :key="s.id" :value="s.id">{{ s.label }} {{ s.time }}</option>
          </select>
        </label>
        <div class="shift-badges">
          <span class="badge ACTIVE">계획 v{{ plan.version }} 활성</span>
          <span class="dummy-tag">계획·지도 더미</span>
        </div>
        <span v-if="unapprovedChanges === 0" class="safety">
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 3 4 6v6c0 4.5 3.4 8.3 8 9 4.6-.7 8-4.5 8-9V6l-8-3z" /><path d="m8.5 12 2.5 2.5 4.5-5" /></svg>
          모든 변경은 사람이 승인했다
        </span>
        <span v-else class="safety bad">⚠ 승인 없이 바뀐 변경 {{ unapprovedChanges }}건</span>
      </section>

      <nav class="rail-nav" aria-label="대시보드 바로 가기">
        <button v-for="n in nav" :key="n.id" :class="{ on: active === n.id }" @click="go(n.id)">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path :d="n.icon" /></svg>
          {{ n.label }}
          <span v-if="n.id === 'agent' && pending.length" class="nav-count">{{ pending.length }}</span>
          <span v-if="n.id === 'dropped'" class="nav-count gray">{{ plan.unserved.length }}</span>
        </button>
      </nav>

      <section class="rail-card stats">
        <p class="eyebrow">근무조 요약</p>
        <dl>
          <div><dt>운행 트럭</dt><dd>{{ plan.routes.length }}대</dd></div>
          <div><dt>정류장</dt><dd>{{ doneStops }}/{{ allStops.length }}곳</dd></div>
          <div><dt>회수할 자전거</dt><dd>{{ movedBikes }}대</dd></div>
          <div><dt>위험 대여소</dt><dd>{{ riskOrder.length }}곳</dd></div>
        </dl>
      </section>
    </aside>

    <div class="content">
      <AskBar :qa="qa" @ask="ask" />

      <!-- 3D 관제 뷰 + 재고 현황 -->
      <section id="view" class="card hero">
        <div class="scene">
          <div class="scene-top">
            <div class="tabs" role="tablist" aria-label="보기 방식">
              <button role="tab" :aria-selected="view === '3d'" :class="{ on: view === '3d' }" @click="view = '3d'">3D 관제 뷰</button>
              <button role="tab" :aria-selected="view === 'map'" :class="{ on: view === 'map' }" @click="view = 'map'">평면 지도</button>
            </div>
            <div class="truck-chips" role="group" aria-label="트럭 경로 골라 보기">
              <button :class="['chip', { on: !focusTruck && !preview }]" :aria-pressed="!focusTruck && !preview" @click="pickTruck(null)">전체</button>
              <button
                v-for="r in plan.routes"
                :key="r.truckId"
                :class="['chip', { on: !preview && focusTruck === r.truckId }]"
                :style="{ '--c': colors[r.truckId] }"
                :aria-pressed="!preview && focusTruck === r.truckId"
                @click="pickTruck(focusTruck === r.truckId && !preview ? null : r.truckId)"
              >
                <i />{{ r.truckId.replace('truck-', 'T') }}
              </button>
            </div>
          </div>

          <IsoStationView
            v-if="view === '3d'"
            class="scene-svg"
            :stations="stations"
            :routes="plan.routes"
            :trucks="trucks"
            :colors="colors"
            :selected="selected"
            :focus-truck="focusTruck"
            :preview="preview"
            @select="selected = $event"
          />
          <div v-else class="flat-map">
            <StationMap
              :stations="stations"
              :routes="plan.routes"
              :trucks="trucks"
              :colors="colors"
              :selected="selected"
              :focus-truck="focusTruck"
              :preview="preview"
              @select="selected = $event"
            />
          </div>
          <div v-if="preview" class="preview-banner" role="status">
            <span class="badge WAITING_APPROVAL">승인 대기 미리보기</span>
            <strong>{{ preview.summary }}</strong>
            <span v-if="preview.truckId" class="pv-legend">
              <span><i class="now" />지금 경로</span><span><i class="next" />바뀐 경로</span><span><b class="rm">✕</b>빠지는 곳</span><span><b class="add">+</b>새로 들르는 곳</span>
            </span>
            <span v-else class="pv-legend"><span><b class="named">!</b>이 요청이 닿는 대여소</span></span>
            <span class="pv-actions">
              <button class="ghost" @click="go('agent')">결정하러 가기</button>
              <button class="ghost" @click="preview = null">미리보기 끄기</button>
            </span>
          </div>
          <div v-if="nextStop" class="next-card">
            <span class="label">다음 도착</span>
            <strong><i :style="{ background: colors[nextStop.truckId] }" />{{ nextStop.truckId.replace('truck-', 'T') }} → {{ nextStop.stationName }}</strong>
            <span class="big">{{ timeOf(nextStop.eta) }} <small>{{ actionLabel[nextStop.action] }} {{ nextStop.bikes }}대</small></span>
            <span class="label">계획 진행</span>
            <div class="bar-row">
              <div class="bar"><span :style="{ width: `${(doneStops / allStops.length) * 100}%` }" /></div>
              <b>{{ Math.round((doneStops / allStops.length) * 100) }}%</b>
            </div>
          </div>

          <ul v-if="view === '3d'" class="iso-legend" aria-label="3D 뷰 읽는 법">
            <li><i class="solid" />기둥 = 지금 재고</li>
            <li><i class="ghost" />투명 상자 = 거치대</li>
            <li><i class="dash" />점선 = 3시간 뒤</li>
            <li class="north" title="북쪽은 화면 오른쪽 위">북 ↗</li>
          </ul>
        </div>

        <aside class="stock">
          <h2>재고 현황 <span class="muted">위험 순</span></h2>
          <ul class="stock-list">
            <li v-for="s in riskOrder.slice(0, 7)" :key="s.stationId">
              <button :class="{ sel: s.stationId === selected }" @click="selected = s.stationId">
                <RingGauge :value="s.bikes / s.docks" :size="42" :stroke="5" :color="riskShades[s.risk][1]" />
                <span class="stock-text">
                  <span class="stock-name">{{ s.name }}</span>
                  <span><b>{{ s.bikes }}</b> / {{ s.docks }} · <span :class="['risk', s.risk]">{{ s.risk === 'EMPTY' ? '부족' : '과잉' }}</span></span>
                </span>
              </button>
            </li>
          </ul>
          <div class="zone-status">
            <h3>권역 상태 <span class="muted">{{ stations.length }}곳</span></h3>
            <div class="stack" role="img" :aria-label="riskCounts.map((r) => `${riskLabel[r.k]} ${r.n}곳`).join(', ')">
              <span v-for="r in riskCounts" :key="r.k" :style="{ flex: r.n, background: riskShades[r.k][1] }">{{ r.n }}</span>
            </div>
            <div class="stack-legend">
              <span><i style="background: var(--empty)" />부족</span>
              <span><i style="background: var(--overflow)" />과잉</span>
              <span><i style="background: var(--ok)" />정상</span>
            </div>
          </div>
        </aside>
      </section>

      <!-- 위젯: 예측 · 진행률 · 트럭 적재 / 선택한 대여소 · 에이전트 대화 -->
      <div class="widgets">
        <section id="trend" class="card w-trend">
          <header class="w-head">
            <h2>3시간 뒤 예측 재고</h2>
            <span class="count-pill gray">위험 {{ Math.min(8, riskOrder.length) }}곳</span>
          </header>
          <ForecastChart :stations="riskOrder.slice(0, 8)" :selected="selected" @select="selected = $event" />
          <p class="chart-legend"><span><i class="dot-fill" />예측</span><span><i class="dot-hollow" />지금</span><span><i class="cap" />거치대 수</span></p>
        </section>

        <section class="card w-ring">
          <header class="w-head"><h2>경로 진행률</h2></header>
          <div class="ring-big">
            <RingGauge :value="doneStops / allStops.length" :size="128" :stroke="13">
              <span class="ring-pct">{{ Math.round((doneStops / allStops.length) * 100) }}%</span>
              <span class="ring-sub">{{ doneStops }}/{{ allStops.length }} 정류장</span>
            </RingGauge>
          </div>
          <ul class="per-truck">
            <li v-for="r in plan.routes" :key="r.truckId" :style="{ '--c': colors[r.truckId] }">
              <span class="t-badge">{{ r.truckId.replace('truck-', 'T') }}</span>
              <div class="mini"><span :style="{ width: `${progress(r)}%` }" /></div>
              <b>{{ progress(r) }}%</b>
            </li>
          </ul>
        </section>

        <section id="truck" class="card w-truck">
          <header class="w-head">
            <h2>트럭 적재</h2>
            <div class="truck-chips small" role="group" aria-label="적재를 볼 트럭">
              <button
                v-for="r in plan.routes"
                :key="r.truckId"
                :class="['chip', { on: loadTruck === r.truckId }]"
                :style="{ '--c': colors[r.truckId] }"
                :aria-pressed="loadTruck === r.truckId"
                @click="loadTruck = r.truckId"
              >
                {{ r.truckId.replace('truck-', 'T') }}
              </button>
            </div>
          </header>
          <div class="truck-stage">
            <TruckLoad :load="loadInfo.t.load" :capacity="loadInfo.t.capacity" :color="colors[loadTruck]!" />
          </div>
          <dl class="truck-facts">
            <div><dt>트럭 · 기사</dt><dd>{{ loadTruck }} · {{ loadInfo.t.driver }}</dd></div>
            <div><dt>상태</dt><dd><i :class="['live', { moving: loadInfo.t.moving }]" />{{ loadInfo.t.moving ? '운행 중' : '정차' }} · {{ loadInfo.t.updatedSec }}초 전</dd></div>
            <div><dt>적재</dt><dd>{{ loadInfo.t.load }} / {{ loadInfo.t.capacity }}대</dd></div>
            <div><dt>남은 정류장</dt><dd>{{ loadInfo.left }}곳</dd></div>
            <div class="wide">
              <dt>다음 정류장</dt>
              <dd v-if="loadInfo.next">{{ loadInfo.next.stationName }} · {{ actionLabel[loadInfo.next.action] }} {{ loadInfo.next.bikes }}대 · {{ timeOf(loadInfo.next.eta) }}</dd>
              <dd v-else>모든 정류장을 마쳤다</dd>
            </div>
          </dl>
          <button class="ghost focus-btn" @click="pickTruck(loadTruck); go('view')">지도에서 이 트럭만 보기</button>
        </section>

        <section v-if="station" class="card w-detail">
          <header class="w-head">
            <h2>선택한 대여소</h2>
            <span class="muted">{{ station.stationId }}</span>
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

        <section id="agent" class="card w-chat">
          <header class="w-head">
            <h2>에이전트 대화</h2>
            <span :class="['feed-state', `is-${feedStatus}`]" role="status" :title="`근무조 ${shiftId}`">{{ feedLabel[feedStatus] }}</span>
            <span :class="['count-pill', { zero: !pending.length }]">승인 대기 {{ pending.length }}</span>
          </header>
          <AgentChat
            :pending="pending"
            :summary="plan.explanation ?? ''"
            :version="plan.version"
            :log="log"
            :previewing="preview?.approvalId"
            :busy="busy"
            :errors="errors"
            @decide="decide"
            @preview="showPreview"
          />
        </section>
      </div>

      <!-- 트럭별 경로 · 빠진 대여소 -->
      <div class="bottom">
        <section id="routes" class="card">
          <header class="w-head"><h2>트럭별 경로</h2><span class="hint">정류장을 누르면 위에서 함께 고른다</span></header>
          <div v-for="r in plan.routes" :key="r.truckId" class="route" :style="{ '--c': colors[r.truckId] }">
            <div class="route-head">
              <span class="t-badge big">{{ r.truckId.replace('truck-', 'T') }}</span>
              <div class="route-name">
                <strong>{{ r.truckId }} · {{ truckOf(r.truckId)?.driver }}</strong>
                <span class="hint">
                  <i :class="['live', { moving: truckOf(r.truckId)?.moving }]" />{{ truckOf(r.truckId)?.moving ? '운행 중' : '정차' }} · 적재 {{ truckOf(r.truckId)?.load }}/{{ truckOf(r.truckId)?.capacity }}
                  <template v-if="legs[r.truckId]"> · {{ r.stops[legs[r.truckId]!.next]!.stationName }}까지 {{ Math.round(legs[r.truckId]!.ratio * 100) }}% 왔다</template>
                </span>
              </div>
              <b class="pct">{{ progress(r) }}%</b>
            </div>
            <ol class="timeline">
              <li
                v-for="(s, i) in r.stops"
                :key="s.seq"
                :class="[s.status, s.action, { sel: s.stationId === selected, next: legs[r.truckId]?.next === i }]"
                :style="legs[r.truckId]?.next === i ? { '--p': legs[r.truckId]!.ratio } : undefined"
                tabindex="0"
                @click="selected = s.stationId"
                @keydown.enter="selected = s.stationId"
              >
                <!-- 트럭이 이 정류장으로 오는 중이면 선을 온 만큼 더 잇고 그 끝에 트럭을 둔다 -->
                <span v-if="legs[r.truckId]?.next === i" class="tl-truck" :title="`${r.truckId} · ${s.stationName}까지 ${Math.round(legs[r.truckId]!.ratio * 100)}% 왔다`" aria-hidden="true">
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M3 7h11v9H3zM14 10h4l3 3v3h-7" /><circle cx="7" cy="18" r="1.6" /><circle cx="17" cy="18" r="1.6" /></svg>
                </span>
                <span class="node">{{ s.status === 'DONE' ? '✓' : s.seq }}</span>
                <span class="tl-name">{{ s.stationName }}</span>
                <span class="tl-sub">{{ actionLabel[s.action] }} {{ s.bikes }} · {{ timeOf(s.eta) }}</span>
              </li>
            </ol>
          </div>
        </section>

        <section id="dropped" class="card">
          <header class="w-head"><h2>이번 계획에서 빠진 대여소</h2><span class="count-pill gray">{{ plan.unserved.length }}</span></header>
          <p class="hint section-hint">이번 계획에서 들르지 않는 곳. 숫자는 우선순위(높을수록 급함), 누르면 이유와 재고를 이 자리에서 펼친다</p>
          <ul class="dropped-list">
            <li v-for="u in plan.unserved" :key="u.stationId" :class="{ sel: u.stationId === selected, open: openDropped === u.stationId }">
              <button class="drop-head" :aria-expanded="openDropped === u.stationId" @click="openDrop(u.stationId)">
                <RingGauge :value="u.priority" :size="40" :stroke="5" color="var(--ink-3)">{{ u.priority.toFixed(2) }}</RingGauge>
                <span class="drop-text">
                  <strong>{{ u.stationName }}</strong>
                  <span v-if="stationOf(u.stationId)" class="hint">
                    재고 {{ stationOf(u.stationId)!.bikes }}/{{ stationOf(u.stationId)!.docks }} · 3시간 뒤 {{ stationOf(u.stationId)!.forecastBikes ?? '-' }}대 ·
                    <span :class="['risk', stationOf(u.stationId)!.risk]">{{ riskLabel[stationOf(u.stationId)!.risk] }}</span>
                  </span>
                </span>
                <svg class="chev" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
              </button>
              <div v-if="openDropped === u.stationId" class="drop-body">
                <p>{{ reasonText(u.reason) }}</p>
                <dl v-if="stationOf(u.stationId)">
                  <div><dt>역할</dt><dd>{{ stationOf(u.stationId)!.role ? roleLabel[stationOf(u.stationId)!.role!] : '역할 없음' }}</dd></div>
                  <div><dt>고도</dt><dd>{{ stationOf(u.stationId)!.elevationM ?? '-' }}m</dd></div>
                  <div><dt>우선순위</dt><dd>{{ u.priority.toFixed(2) }}</dd></div>
                </dl>
                <button class="ghost" @click="go('view')">지도에서 보기</button>
              </div>
            </li>
          </ul>
        </section>
      </div>
    </div>
  </div>

  <Transition name="toast"><div v-if="toast" class="toast" role="status">{{ toast }}</div></Transition>
</template>

<style scoped>
.dash { display: grid; grid-template-columns: 232px minmax(0, 1fr); gap: 18px; align-items: start; }
.content { display: flex; flex-direction: column; gap: 18px; min-width: 0; }
section[id] { scroll-margin-top: 76px; }
.eyebrow { margin: 0; font-size: 11px; font-weight: 700; color: var(--brand-strong); letter-spacing: 0.03em; display: flex; align-items: center; gap: 6px; }
.w-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 12px; flex-wrap: wrap; }
.w-head h2 { margin: 0; }

/* 레일 */
.rail { position: sticky; top: 76px; display: flex; flex-direction: column; gap: 12px; }
.rail-card { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); padding: 14px; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 4px; }
.shift { background: var(--sky); border-color: #d3e7f3; }
.region { font-size: 20px; font-weight: 800; letter-spacing: -0.02em; }
.slot select { width: 100%; padding: 5px 8px; font-size: 13px; color: var(--ink-2); }
.shift-badges { margin: 6px 0 4px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.shift-badges .badge { margin: 0; }
/* 에이전트 소식(SSE) 연결 상태 */
.feed-state { margin-left: auto; display: inline-flex; align-items: center; gap: 5px; font-size: 11px; font-weight: 700; color: var(--ink-3); white-space: nowrap; }
.feed-state::before { content: ''; width: 7px; height: 7px; border-radius: 50%; background: #94a3b8; }
.feed-state.is-live { color: var(--brand-strong); }
.feed-state.is-live::before { background: var(--brand); box-shadow: 0 0 0 3px var(--brand-soft); }
.feed-state.is-offline { color: #b45309; }
.feed-state.is-offline::before { background: #f59e0b; }
.safety { display: inline-flex; align-items: center; gap: 5px; align-self: flex-start; padding: 3px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; color: #166534; background: rgb(255 255 255 / 0.85); border: 1px solid #bbf7d0; }
.safety.bad { color: #991b1b; background: var(--empty-soft); border-color: #fecaca; }
.rail-nav { display: flex; flex-direction: column; gap: 2px; padding: 6px; background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); }
.rail-nav button { display: flex; align-items: center; gap: 10px; padding: 9px 10px; background: transparent; color: var(--ink-2); font-weight: 600; font-size: 13px; text-align: left; border-radius: 10px; }
.rail-nav button:hover:not(:disabled) { background: #f4f7f9; }
.rail-nav button.on { background: var(--brand); color: #fff; box-shadow: 0 4px 10px rgb(15 157 88 / 0.25); }
.nav-count { margin-left: auto; min-width: 20px; padding: 0 6px; border-radius: 999px; font-size: 11px; line-height: 18px; text-align: center; background: var(--overflow); color: #fff; }
.nav-count.gray { background: #e2e8f0; color: var(--ink-2); }
.rail-nav button.on .nav-count { background: #fff; color: var(--brand-strong); }
.stats dl { margin: 6px 0 0; display: flex; flex-direction: column; gap: 6px; }
.stats dl div { display: flex; justify-content: space-between; font-size: 13px; }
.stats dt { color: var(--ink-3); }
.stats dd { margin: 0; font-weight: 800; }

/* 3D 관제 뷰 */
.hero { display: grid; grid-template-columns: minmax(0, 1fr) 236px; padding: 0; overflow: hidden; }
.scene { position: relative; min-height: 560px; background: var(--sky); display: flex; flex-direction: column; }
.scene-svg { flex: 1; padding: 56px 8px 8px; max-height: 640px; }
.flat-map { padding: 60px 16px 16px; }
.scene-top { position: absolute; top: 14px; left: 14px; right: 14px; display: flex; gap: 10px; flex-wrap: wrap; z-index: 2; }
.tabs { display: inline-flex; padding: 3px; background: #fff; border-radius: 999px; box-shadow: var(--shadow); }
.tabs button { padding: 6px 14px; font-size: 13px; background: transparent; color: var(--ink-2); border-radius: 999px; }
.tabs button:hover:not(:disabled) { background: #f4f7f9; }
.tabs button.on { background: var(--brand); color: #fff; }
.truck-chips { display: flex; gap: 4px; padding: 3px; background: #fff; border-radius: 999px; box-shadow: var(--shadow); }
.truck-chips.small { box-shadow: none; background: #f1f5f9; }
.chip { --c: var(--brand); display: inline-flex; align-items: center; gap: 5px; padding: 5px 11px; font-size: 12px; font-weight: 700; border-radius: 999px; background: transparent; color: var(--ink-2); }
.chip i { width: 8px; height: 8px; border-radius: 50%; background: var(--c); }
.chip:hover:not(:disabled) { background: #f4f7f9; }
.chip.on, .chip.on:hover:not(:disabled) { background: var(--c); color: #fff; }
.chip.on i { background: #fff; }
.next-card { position: absolute; left: 14px; bottom: 14px; width: 232px; display: flex; flex-direction: column; gap: 3px; padding: 12px 14px; background: rgb(255 255 255 / 0.94); border-radius: 14px; box-shadow: 0 8px 24px rgb(15 23 42 / 0.1); }
.next-card .label { font-size: 11px; font-weight: 700; color: var(--ink-3); }
.next-card .label:nth-of-type(2) { margin-top: 8px; }
.next-card strong { display: flex; align-items: center; gap: 6px; font-size: 13px; }
.next-card strong i { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.next-card .big { font-size: 24px; font-weight: 800; color: var(--brand-strong); letter-spacing: -0.02em; }
.next-card .big small { font-size: 12px; color: var(--ink-2); font-weight: 700; }
.bar-row { display: flex; align-items: center; gap: 8px; }
.bar-row b { font-size: 13px; }
.bar { flex: 1; height: 6px; border-radius: 999px; background: #e2e8f0; overflow: hidden; }
.bar span { display: block; height: 100%; background: var(--brand); border-radius: 999px; }
.iso-legend { position: absolute; right: 14px; bottom: 14px; display: flex; gap: 12px; flex-wrap: wrap; margin: 0; padding: 7px 12px; list-style: none; font-size: 11.5px; font-weight: 600; color: var(--ink-2); background: rgb(255 255 255 / 0.92); border-radius: 999px; box-shadow: var(--shadow); }
.iso-legend li { display: inline-flex; align-items: center; gap: 5px; }
.iso-legend i { width: 10px; height: 10px; display: inline-block; border-radius: 2px; }
.iso-legend .solid { background: linear-gradient(135deg, #f87171, #f59e0b 50%, #10b981); }
.iso-legend .ghost { border: 1.5px solid #94a3b8; background: rgb(255 255 255 / 0.4); }
.iso-legend .dash { border: 1.5px dashed #475569; }
.iso-legend .north { padding-left: 10px; border-left: 1px solid var(--line); color: var(--brand-strong); font-weight: 800; }
.preview-banner { position: absolute; top: 58px; left: 14px; max-width: min(560px, calc(100% - 28px)); z-index: 2; display: flex; flex-wrap: wrap; align-items: center; gap: 6px 10px; padding: 10px 12px; background: rgb(255 255 255 / 0.96); border: 1px solid #fdba74; border-left: 4px solid #ea580c; border-radius: 12px; box-shadow: 0 8px 24px rgb(15 23 42 / 0.1); font-size: 13px; }
.preview-banner .badge { margin: 0; }
.preview-banner strong { flex: 1 1 260px; }
.pv-legend { display: flex; flex-wrap: wrap; gap: 10px; width: 100%; font-size: 11.5px; font-weight: 600; color: var(--ink-2); }
.pv-legend span { display: inline-flex; align-items: center; gap: 4px; }
.pv-legend i { width: 18px; height: 0; border-top: 3px dotted var(--ink-3); }
.pv-legend i.next { border-top: 3px dashed #ea580c; }
.pv-legend b { width: 16px; height: 16px; border-radius: 50%; display: grid; place-items: center; color: #fff; font-size: 10px; }
.pv-legend .rm { background: #dc2626; }
.pv-legend .add { background: #059669; }
.pv-legend .named { background: #ea580c; }
.pv-actions { display: flex; gap: 6px; margin-left: auto; }
.pv-actions button { padding: 4px 10px; font-size: 12px; }

/* 재고 현황 (오른쪽 패널) */
.stock { display: flex; flex-direction: column; gap: 10px; padding: 16px 14px; border-left: 1px solid var(--line); background: #fff; }
.stock h2 { margin: 0; justify-content: space-between; }
.stock h2 .muted { font-size: 11px; font-weight: 600; }
.stock-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 6px; }
.stock-list button { width: 100%; display: flex; align-items: center; gap: 10px; padding: 7px 8px; background: #f8fafc; color: inherit; border: 1px solid transparent; border-radius: 12px; text-align: left; font-weight: 500; }
.stock-list button:hover:not(:disabled) { background: #f1f5f9; }
.stock-list button.sel { border-color: var(--brand); background: var(--brand-soft); }
.stock-list :deep(.center) { font-size: 10.5px; }
.stock-text { display: flex; flex-direction: column; min-width: 0; font-size: 12px; color: var(--ink-3); }
.stock-text b { color: var(--ink); font-size: 15px; }
.stock-name { font-weight: 700; color: var(--ink); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.risk { font-weight: 700; }
.risk.EMPTY { color: var(--empty); }
.risk.OVERFLOW { color: #b45309; }
.zone-status { margin-top: auto; padding-top: 10px; border-top: 1px solid var(--line); }
.zone-status h3 { margin: 0 0 8px; font-size: 13px; display: flex; justify-content: space-between; }
.zone-status h3 .muted { font-size: 11px; }
.stack { display: flex; height: 26px; border-radius: 8px; overflow: hidden; gap: 2px; }
.stack span { display: grid; place-items: center; color: #fff; font-size: 12px; font-weight: 800; min-width: 22px; }
.stack-legend { display: flex; gap: 10px; margin-top: 6px; font-size: 11px; color: var(--ink-3); font-weight: 600; }
.stack-legend span { display: inline-flex; align-items: center; gap: 4px; }
.stack-legend i { width: 8px; height: 8px; border-radius: 50%; }

/* 위젯 */
.widgets { display: grid; grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr) minmax(0, 1.15fr); grid-template-areas: 'trend ring truck' 'detail chat truck'; gap: 18px; align-items: stretch; }
.w-trend { grid-area: trend; }
.w-ring { grid-area: ring; }
.w-truck { grid-area: truck; display: flex; flex-direction: column; }
.w-detail { grid-area: detail; }
.w-chat { grid-area: chat; display: flex; flex-direction: column; }
/* 에이전트 대화는 내용이 길어도 줄 높이를 키우지 않고 옆 카드 높이에 맞춰 안에서 스크롤한다.
   대화가 줄 높이를 정하면 같은 줄의 '선택한 대여소' 카드 아래가 크게 빈다. */
@media (min-width: 861px) {
  .widgets { grid-template-rows: auto minmax(320px, auto); }
  .w-chat { height: 0; min-height: 100%; }
  .w-chat :deep(.body) { max-height: none; }
}
.count-pill { background: var(--overflow); color: #fff; border-radius: 999px; font-size: 11px; font-weight: 700; padding: 2px 9px; }
.count-pill.zero { background: var(--ok); }
.count-pill.gray { background: #eef2f6; color: var(--ink-2); }
.chart-legend { display: flex; gap: 14px; margin: 6px 0 0; font-size: 11px; color: var(--ink-3); font-weight: 600; }
.chart-legend span { display: inline-flex; align-items: center; gap: 5px; }
.dot-fill { width: 9px; height: 9px; border-radius: 50%; background: var(--ink-2); }
.dot-hollow { width: 9px; height: 9px; border-radius: 50%; border: 2px solid var(--ink-2); }
.chart-legend .cap { width: 10px; height: 10px; border-radius: 3px; background: #dde5ec; }
.ring-big { display: grid; place-items: center; padding: 6px 0 14px; }
.ring-pct { display: block; font-size: 30px; letter-spacing: -0.03em; }
.ring-sub { display: block; font-size: 11px; color: var(--ink-3); font-weight: 600; }
.per-truck { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 8px; }
.per-truck li { display: flex; align-items: center; gap: 10px; font-size: 12px; }
.t-badge { flex: none; width: 26px; height: 22px; border-radius: 7px; background: var(--c); color: #fff; font-size: 11px; font-weight: 800; display: grid; place-items: center; }
.t-badge.big { width: 36px; height: 36px; border-radius: 11px; font-size: 13px; }
.mini { flex: 1; height: 6px; border-radius: 999px; background: #eef2f6; overflow: hidden; }
.mini span { display: block; height: 100%; background: var(--c); border-radius: 999px; }
.per-truck b { min-width: 34px; text-align: right; }
.truck-stage { margin: 0 -8px; padding: 6px 0; border-radius: 12px; background: linear-gradient(180deg, #eef6fb, #fff); }
.truck-facts { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }
.truck-facts div, .st-facts div { padding: 8px 10px; background: #f8fafc; border-radius: 10px; min-width: 0; }
.truck-facts .wide { grid-column: 1 / -1; }
dt { color: var(--ink-3); font-size: 11px; font-weight: 600; }
dd { margin: 2px 0 0; font-weight: 700; font-size: 13px; }
.live { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--ink-3); margin-right: 5px; vertical-align: 1px; }
.live.moving { background: var(--ok); box-shadow: 0 0 0 3px rgb(16 185 129 / 0.2); }
.focus-btn { margin-top: auto; width: 100%; }
.st-head { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; }
.st-icon { flex: none; width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center; }
.st-head strong { font-size: 16px; }
.st-chips { display: flex; gap: 4px; margin-top: 4px; flex-wrap: wrap; }
.tag { padding: 2px 9px; border-radius: 999px; font-size: 11px; font-weight: 700; background: #eef2f6; color: var(--ink-2); }
.tag.NONE { background: var(--brand-soft); color: var(--brand-strong); }
.tag.EMPTY { background: var(--empty-soft); color: #b91c1c; }
.tag.OVERFLOW { background: var(--overflow-soft); color: #92400e; }
.st-facts { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; margin: 0 0 10px; }
.stock-bar { height: 8px; background: #eef2f6; border-radius: 999px; overflow: hidden; margin-bottom: 12px; }
.stock-bar span { display: block; height: 100%; border-radius: 999px; }
.plan-note { border-left: 4px solid var(--c); background: #f8fafc; border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 4px; font-size: 13px; }
.plan-note.dropped { --c: var(--ink-3); }

/* 트럭별 경로 타임라인 · 빠진 대여소 */
.bottom { display: grid; grid-template-columns: minmax(0, 2fr) minmax(280px, 1fr); gap: 18px; align-items: start; }
.route { padding: 14px 0; border-top: 1px solid #f1f5f9; }
.route:first-of-type { border-top: 0; padding-top: 0; }
.route-head { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
.route-name { display: flex; flex-direction: column; flex: 1; }
.route-head .pct { font-size: 15px; color: var(--c); }
/* 정류장이 많을 때만 가로로 민다. 위쪽 여백은 고른 정류장의 테두리(box-shadow 4px)가 스크롤 상자에 잘리지 않을 자리다. */
.timeline { list-style: none; display: flex; margin: 0; padding: 6px 0 6px; overflow-x: auto; }
.timeline li { position: relative; flex: 1 0 96px; display: flex; flex-direction: column; align-items: center; gap: 3px; padding: 0 4px; text-align: center; cursor: pointer; border-radius: 10px; outline: none; }
.timeline li::before { content: ''; position: absolute; top: 13px; left: calc(-50% + 16px); right: calc(50% + 16px); border-top: 2px dashed #cbd5e1; }
.timeline li:first-child::before { display: none; }
.timeline li.DONE::before { border-top: 3px solid var(--c); }
/* 다음 정류장으로 가는 중: 온 비율(--p)만큼 실선을 더 긋는다 */
.timeline li.next::after { content: ''; position: absolute; top: 12.5px; left: calc(-50% + 16px); width: calc((100% - 32px) * var(--p, 0)); border-top: 3px solid var(--c); }
.tl-truck { position: absolute; z-index: 1; top: 14px; left: calc(-50% + 16px + (100% - 32px) * var(--p, 0)); transform: translate(-50%, -50%); width: 24px; height: 18px; border-radius: 6px; display: grid; place-items: center; background: var(--c); color: #fff; box-shadow: 0 0 0 2px #fff; }
.timeline li:focus-visible .node { outline: 2px solid var(--c); outline-offset: 2px; }
.node { position: relative; width: 28px; height: 28px; border-radius: 50%; display: grid; place-items: center; font-size: 12px; font-weight: 800; background: #fff; color: var(--c); border: 2.5px solid var(--c); }
.timeline li.DONE .node { background: var(--c); color: #fff; }
.timeline li.sel .node { box-shadow: 0 0 0 4px color-mix(in srgb, var(--c) 25%, transparent); }
.timeline li:hover .tl-name, .timeline li:focus-visible .tl-name { text-decoration: underline; }
.tl-name { font-size: 12px; font-weight: 700; color: var(--ink); word-break: keep-all; }
.timeline li.DONE .tl-name { color: var(--ink-3); }
.tl-sub { font-size: 11px; color: var(--ink-3); }
.section-hint { display: block; margin: -6px 0 12px; line-height: 1.5; }
.dropped-list { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.dropped-list li { border: 1px dashed var(--line-2); border-radius: 12px; }
.dropped-list li.sel { border: 1px solid var(--brand); background: var(--brand-soft); }
.drop-head { width: 100%; display: flex; align-items: center; gap: 10px; padding: 10px 12px; background: transparent; color: inherit; font-weight: 500; text-align: left; border-radius: 12px; }
.drop-head:hover:not(:disabled) { background: rgb(15 23 42 / 0.03); }
.drop-text { display: flex; flex-direction: column; gap: 2px; font-size: 13px; min-width: 0; flex: 1; }
.chev { flex: none; color: var(--ink-3); transition: transform 0.15s; }
.dropped-list li.open .chev { transform: rotate(180deg); }
.drop-body { padding: 0 12px 12px 62px; display: flex; flex-direction: column; gap: 8px; font-size: 12.5px; color: var(--ink-2); }
.drop-body p { margin: 0; line-height: 1.5; }
.drop-body dl { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; margin: 0; }
.drop-body dl div { padding: 6px 8px; background: #fff; border-radius: 8px; }
.drop-body button { align-self: flex-start; padding: 5px 12px; font-size: 12px; }
.dropped-list :deep(.center) { font-size: 10px; }

.toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%); background: var(--ink); color: #fff; padding: 10px 18px; border-radius: 999px; font-size: 14px; box-shadow: var(--shadow); z-index: 20; }
.toast-enter-active, .toast-leave-active { transition: all 0.2s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translate(-50%, 8px); }

/* 레일을 위로 올리고 위젯을 두 줄로 */
@media (max-width: 1240px) {
  .dash { grid-template-columns: minmax(0, 1fr); }
  .rail { position: static; display: grid; grid-template-columns: 1fr 1fr; }
  .rail-nav { display: none; }
  .widgets { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); grid-template-areas: 'trend ring' 'truck chat' 'detail detail'; }
}
@media (max-width: 860px) {
  .hero { grid-template-columns: minmax(0, 1fr); }
  .stock { border-left: 0; border-top: 1px solid var(--line); }
  .bottom { grid-template-columns: minmax(0, 1fr); }
  .widgets { grid-template-columns: minmax(0, 1fr); grid-template-areas: 'trend' 'ring' 'truck' 'detail' 'chat'; }
  .st-facts { grid-template-columns: 1fr 1fr; }
}
@media (max-width: 640px) {
  .rail { grid-template-columns: minmax(0, 1fr); }
  .scene { min-height: 0; }
  .scene-svg { padding-top: 96px; }
  .next-card, .iso-legend { position: static; width: auto; margin: 0 12px 12px; }
  .iso-legend { border-radius: 12px; }
}
</style>
