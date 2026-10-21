<!--
기사 앱 (PRD 10장, FR-10~14). 모바일 웹, 큰 버튼. 자료는 useDriver가 읽는다:
경로 GET /api/drivers/me/route, 구역 대여소(길안내 좌표·앱 재고), 변경 제안(승인 대기 작업의 기사 카드), 보고 POST /api/reports.
운행 중에는 모달 없이 배지만 보이고, 정차하면 변경 제안 카드를 연다 (FR-14). 운행·정차는 기사가 직접 누른다 (위치 신호가 없다).
-->
<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Schemas } from '../../shared/api/client'
import { useAuth } from '../../shared/auth/store'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'
import { useDriver } from './useDriver'

type Stop = Schemas['Stop']
type Issue = NonNullable<Schemas['FieldReportRequest']['issue']>

const auth = useAuth()
const truckId = computed(() => auth.user?.truckId ?? '')
const driver = useDriver(() => truckId.value)
const { route, routeState, routeError, stations, cards, waitingManager } = driver
const stops = computed(() => route.value?.stops ?? [])
const capacity = computed(() => route.value?.capacity ?? 15)
const moving = ref(true)

const current = computed(() => stops.value.find((s) => (s.status ?? 'PENDING') === 'PENDING'))
const rest = computed(() => stops.value.filter((s) => (s.status ?? 'PENDING') === 'PENDING' && s !== current.value))
const done = computed(() => stops.value.filter((s) => (s.status ?? 'PENDING') !== 'PENDING'))
// 지금 적재: 마지막으로 끝낸 정류장의 적재, 없으면 출발 적재 (backend 트럭 상태와 같은 규칙)
const load = computed(() => stops.value.filter((s) => s.status === 'DONE').at(-1)?.loadAfter ?? route.value?.startLoad ?? 0)
// 정류장 이름은 backend가 기준 정보에서 붙인다. 기준 정보에 없는 대여소면 ID다.
const nameOf = (s: Stop) => s.stationName ?? s.stationId
const station = computed(() => (current.value ? stations.value.get(current.value.stationId) : undefined))
// 구역 대여소 좌표가 있으면 길찾기, 없으면 이름 검색
const naviUrl = computed(() => {
  if (!current.value) return '#'
  const s = station.value
  return s
    ? `https://map.kakao.com/link/to/${encodeURIComponent(s.name)},${s.lat},${s.lon}`
    : `https://map.kakao.com/link/search/${encodeURIComponent(nameOf(current.value))}`
})

// 기사가 결정할 변경 제안 (재경로 작업의 기사 카드). 한 번에 한 장씩 연다.
const proposal = computed(() => cards.value[0] ?? null)
const deciding = ref(false)
const decideError = ref('')

// 정류장 보고 시트 (FR-11, FR-12). 기본값은 계획 수량과 앱 재고라 그대로면 [도착·보고] → [완료] 두 번이면 끝난다.
const sheet = ref(false)
const qty = ref(0)
const arrival = ref(0)
const issue = ref<Issue | null>(null)
const memo = ref('')
const sending = ref(false)
const sendError = ref('')
const issues: { value: Issue; label: string; icon: string }[] = [
  { value: 'BLOCKED', label: '진입 불가', icon: '⛔' },
  { value: 'CONSTRUCTION', label: '공사', icon: '🚧' },
  { value: 'INVENTORY_MISMATCH', label: '재고 다름', icon: '🔢' },
  { value: 'OTHER', label: '기타', icon: '✏️' },
]
// 진입 불가·공사는 그 정류장을 처리하지 못한 보고라 실은·내린 대수를 보내지 않는다 (backend가 SKIPPED로 남긴다)
const unserved = computed(() => issue.value === 'BLOCKED' || issue.value === 'CONSTRUCTION')
const toast = ref('')

function openReport() {
  if (!current.value) return
  qty.value = current.value.bikes
  arrival.value = station.value?.bikes ?? 0
  issue.value = null
  memo.value = ''
  sendError.value = ''
  sheet.value = true
}

async function submitReport() {
  const s = current.value
  if (!s || !route.value || sending.value) return
  sending.value = true
  sendError.value = ''
  const moved = unserved.value ? null : qty.value
  try {
    const taskId = await driver.report({
      shiftId: route.value.shiftId,
      stationId: s.stationId,
      actualBikes: arrival.value,
      loaded: s.action === 'PICKUP' ? moved : null,
      unloaded: s.action === 'DROPOFF' ? moved : null,
      issue: issue.value,
      memo: issue.value && memo.value.trim() ? memo.value.trim() : null,
    })
    sheet.value = false
    notify(taskId ? '보고했다. 남은 경로를 다시 계산한다' : `${nameOf(s)} 완료`)
  } catch (e) {
    sendError.value = e instanceof Error ? e.message : String(e)
  } finally {
    sending.value = false
  }
}

async function decide(body: ApprovalDecision) {
  const card = proposal.value
  if (!card || deciding.value) return
  deciding.value = true
  decideError.value = ''
  try {
    await driver.decide(card, body)
    notify(body.decision === 'APPROVE' ? '변경을 수락했다. 경로가 곧 바뀐다' : '기존 경로를 유지한다')
  } catch (e) {
    decideError.value = e instanceof Error ? e.message : String(e)
  } finally {
    deciding.value = false
  }
}

function notify(text: string) {
  toast.value = text
  window.setTimeout(() => (toast.value = ''), 2600)
}

const time = (iso?: string | null) => (iso ? new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) : '')
const actionText: Record<Stop['action'], string> = { PICKUP: '싣기', DROPOFF: '내리기' }
</script>

<template>
  <div class="app">
    <header class="bar">
      <div class="who">
        <span class="truck">🚚 {{ truckId }}</span>
        <span class="sub">{{ auth.user?.name }} · 오늘 경로</span>
      </div>
      <div class="bar-right">
        <span v-if="route && cards.length && moving" class="notice" :aria-label="`변경 제안 ${cards.length}건`">변경 제안 {{ cards.length }}</span>
        <button class="icon" aria-label="로그아웃" @click="auth.logout()">⎋</button>
      </div>
    </header>

    <div v-if="route" class="status">
      <button :class="['drive', { moving }]" @click="moving = !moving">
        <span><span class="led" />{{ moving ? '운행 중' : '정차 중' }}</span>
        <small>눌러서 전환</small>
      </button>
      <div class="load">
        <span>적재 <strong>{{ load }}</strong>/{{ capacity }}</span>
        <div class="gauge"><span :style="{ width: `${(load / capacity) * 100}%` }" /></div>
      </div>
    </div>

    <div v-if="route" class="steps">
      <span v-for="s in stops" :key="s.seq" :class="['pip', s.status, { cur: s === current }]" />
      <span class="steps-text">{{ done.length }}/{{ stops.length }} 완료</span>
    </div>

    <!-- 정차 중에만 변경 제안을 펼친다 (FR-14) -->
    <section v-if="route && proposal && !moving" class="proposal">
      <ApprovalCard :card="proposal" :busy="deciding" :error="decideError" @decide="decide" />
    </section>
    <p v-else-if="route && proposal" class="moving-hint">운행 중이라 변경 제안은 정차하면 보여 준다</p>
    <p v-if="route && waitingManager" class="manager-hint">관리자 승인을 기다리는 내 경로 변경 {{ waitingManager }}건 (승인되면 여기서 수락한다)</p>

    <section v-if="routeState === 'loading' && !route" class="now finished">
      <p class="reason">오늘 경로를 읽는 중</p>
    </section>
    <section v-else-if="routeState === 'error'" class="now finished">
      <h1>경로를 읽지 못했다</h1>
      <p class="reason">{{ routeError }}</p>
      <button class="btn ghost" @click="driver.refresh">다시 시도</button>
    </section>
    <section v-else-if="!route" class="now finished">
      <h1>오늘 배정된 경로가 없다</h1>
      <p class="reason">관리자가 {{ truckId || '내 트럭' }} 계획을 승인하면 여기에 나온다.</p>
      <button class="btn ghost" @click="driver.refresh">새로고침</button>
    </section>
    <section v-else-if="current" class="now">
      <div class="now-top">
        <span class="seq">{{ current.seq }}</span>
        <div>
          <span class="eyebrow">다음 정류장 · {{ time(current.eta) }} 도착 예정</span>
          <h1>{{ nameOf(current) }}</h1>
        </div>
      </div>
      <div :class="['action', current.action]">
        <span>{{ actionText[current.action] }}</span>
        <strong>{{ current.bikes }}<small>대</small></strong>
      </div>
      <p class="reason">{{ current.reason }}</p>
      <div class="now-btns">
        <a class="btn ghost" :href="naviUrl" target="_blank" rel="noopener">🧭 길안내</a>
        <button class="btn primary" @click="openReport">도착 · 보고</button>
      </div>
    </section>
    <section v-else class="now finished">
      <span class="big">🎉</span>
      <h1>오늘 경로 끝</h1>
      <p class="reason">수고했다. 차고지로 복귀한다.</p>
    </section>

    <section v-if="rest.length" class="list">
      <h2>남은 정류장</h2>
      <ol>
        <li v-for="s in rest" :key="s.seq">
          <span class="seq small">{{ s.seq }}</span>
          <div class="li-main">
            <strong>{{ nameOf(s) }}</strong>
            <span class="hint">{{ time(s.eta) }}</span>
          </div>
          <span :class="['qty', s.action]">{{ actionText[s.action] }} {{ s.bikes }}</span>
        </li>
      </ol>
    </section>

    <section v-if="done.length" class="list done">
      <h2>끝난 정류장</h2>
      <ol>
        <li v-for="s in done" :key="s.seq">
          <span class="seq small">✓</span>
          <div class="li-main"><strong>{{ nameOf(s) }}</strong></div>
          <span class="qty">{{ actionText[s.action] }} {{ s.bikes }}</span>
        </li>
      </ol>
    </section>

    <!-- 보고 시트 -->
    <Transition name="sheet">
      <div v-if="sheet && current" class="backdrop" @click.self="sheet = false">
        <div class="sheet" role="dialog" aria-label="정류장 보고">
          <div class="grip" />
          <h2>{{ nameOf(current) }}</h2>
          <template v-if="!unserved">
            <p class="hint">실제로 {{ actionText[current.action] }} 대수 · 계획 {{ current.bikes }}대</p>
            <div class="stepper">
              <button aria-label="하나 줄이기" :disabled="qty <= 0" @click="qty--">−</button>
              <strong>{{ qty }}</strong>
              <button aria-label="하나 늘리기" @click="qty++">+</button>
            </div>
          </template>
          <p class="hint">도착했을 때 대여소 재고 · 앱 {{ station?.bikes ?? '-' }}대</p>
          <div class="stepper small">
            <button aria-label="도착 재고 하나 줄이기" :disabled="arrival <= 0" @click="arrival--">−</button>
            <strong>{{ arrival }}</strong>
            <button aria-label="도착 재고 하나 늘리기" @click="arrival++">+</button>
          </div>
          <div class="issues">
            <button
              v-for="i in issues"
              :key="i.value"
              :class="['issue', { on: issue === i.value }]"
              @click="issue = issue === i.value ? null : i.value"
            >
              <span>{{ i.icon }}</span>{{ i.label }}
            </button>
          </div>
          <input v-if="issue" v-model="memo" class="memo" maxlength="200" placeholder="짧은 메모 (선택)" />
          <p v-if="sendError" class="send-error" role="alert">보내지 못했다: {{ sendError }}</p>
          <button class="btn primary block" :disabled="sending" @click="submitReport">{{ sending ? '보내는 중' : '완료' }}</button>
        </div>
      </div>
    </Transition>

    <Transition name="toast"><div v-if="toast" class="toast">{{ toast }}</div></Transition>
  </div>
</template>

<style scoped>
.app { max-width: 480px; margin: 0 auto; min-height: 100vh; background: #f1f5f4; padding-bottom: 32px; }
.bar { display: flex; justify-content: space-between; align-items: center; padding: 14px 18px; background: var(--brand); color: #fff; position: sticky; top: 0; z-index: 5; }
.who { display: flex; flex-direction: column; }
.truck { font-weight: 800; font-size: 18px; }
.sub { font-size: 12px; opacity: 0.85; }
.bar-right { display: flex; gap: 10px; align-items: center; }
.notice { background: #fff; color: #b45309; font-weight: 800; font-size: 12px; padding: 4px 10px; border-radius: 999px; }
.icon { background: rgb(255 255 255 / 0.15); padding: 6px 10px; font-size: 16px; }

.status { display: grid; grid-template-columns: auto 1fr; gap: 12px; padding: 14px 18px 0; align-items: center; }
.drive { display: flex; flex-direction: column; align-items: flex-start; gap: 0; background: #fff; color: var(--ink); border: 1px solid var(--line); padding: 8px 14px; font-size: 15px; }
.drive:hover:not(:disabled) { background: #fff; }
.drive small { font-size: 10px; color: var(--ink-3); font-weight: 500; }
.led { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--ink-3); margin-right: 6px; vertical-align: middle; }
.drive.moving .led { background: var(--ok); box-shadow: 0 0 0 4px rgb(16 185 129 / 0.2); }
.load { display: flex; flex-direction: column; gap: 6px; font-size: 14px; }
.gauge { height: 10px; background: #dfe7e4; border-radius: 999px; overflow: hidden; }
.gauge span { display: block; height: 100%; background: var(--brand); border-radius: 999px; transition: width 0.3s; }

.steps { display: flex; align-items: center; gap: 5px; padding: 14px 18px 4px; }
.pip { flex: 1; height: 6px; border-radius: 999px; background: #d6dfdc; }
.pip.DONE { background: var(--brand); }
.pip.cur { background: var(--overflow); }
.steps-text { font-size: 12px; color: var(--ink-3); margin-left: 6px; white-space: nowrap; }

.proposal { padding: 8px 14px 0; }
.proposal :deep(.approval) { margin: 0; background: #fff; }
.proposal :deep(.actions button) { padding: 14px 20px; font-size: 16px; }
.moving-hint { margin: 8px 18px 0; font-size: 13px; color: #92400e; background: var(--overflow-soft); padding: 8px 12px; border-radius: 10px; }

.now { margin: 12px 14px 0; background: #fff; border-radius: 20px; padding: 20px; box-shadow: 0 6px 24px rgb(15 23 42 / 0.08); }
.now-top { display: flex; gap: 12px; align-items: center; }
.now h1 { font-size: 24px; margin: 2px 0 0; }
.eyebrow { font-size: 12px; color: var(--ink-3); font-weight: 600; }
.seq { width: 40px; height: 40px; flex: 0 0 auto; border-radius: 12px; display: grid; place-items: center; font-weight: 800; font-size: 18px; background: var(--ink); color: #fff; }
.seq.small { width: 28px; height: 28px; border-radius: 8px; font-size: 13px; background: #e2e8f0; color: var(--ink-2); }
.action { margin: 18px 0 8px; display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-radius: 16px; font-size: 20px; font-weight: 800; }
.action strong { font-size: 44px; line-height: 1; }
.action small { font-size: 18px; margin-left: 2px; }
.action.DROPOFF { background: #dcfce7; color: #15803d; }
.action.PICKUP { background: #e0f2fe; color: #0369a1; }
.reason { color: var(--ink-3); font-size: 14px; margin: 0 0 16px; }
.now-btns { display: grid; grid-template-columns: 1fr 1.6fr; gap: 10px; }
.btn { display: grid; place-items: center; padding: 18px; border-radius: 14px; font-size: 18px; font-weight: 800; text-decoration: none; }
.btn.ghost { background: #f1f5f9; color: var(--ink); border: 0; }
.btn.primary { background: var(--brand); color: #fff; }
.btn.block { width: 100%; margin-top: 18px; }
.finished { text-align: center; }
.big { font-size: 48px; }

.list { margin: 18px 14px 0; }
.list h2 { font-size: 14px; color: var(--ink-3); margin: 0 4px 8px; }
.list ol { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 8px; }
.list li { display: flex; align-items: center; gap: 12px; background: #fff; padding: 12px 14px; border-radius: 14px; }
.li-main { flex: 1; display: flex; flex-direction: column; font-size: 15px; }
.qty { font-weight: 700; font-size: 14px; padding: 4px 10px; border-radius: 8px; background: #f1f5f9; }
.qty.DROPOFF { background: #dcfce7; color: #15803d; }
.qty.PICKUP { background: #e0f2fe; color: #0369a1; }
.list.done li { opacity: 0.6; }

.backdrop { position: fixed; inset: 0; background: rgb(15 23 42 / 0.45); display: flex; align-items: flex-end; justify-content: center; z-index: 20; }
.sheet { width: 100%; max-width: 480px; background: #fff; border-radius: 24px 24px 0 0; padding: 12px 20px 28px; }
.grip { width: 40px; height: 5px; border-radius: 999px; background: #cbd5e1; margin: 0 auto 14px; }
.sheet h2 { font-size: 20px; margin: 0; }
.stepper { display: grid; grid-template-columns: 72px 1fr 72px; align-items: center; margin: 18px 0; }
.stepper button { height: 72px; font-size: 34px; border-radius: 18px; background: #f1f5f9; color: var(--ink); }
.stepper button:hover:not(:disabled) { background: #e2e8f0; }
.stepper strong { text-align: center; font-size: 56px; }
.stepper.small { grid-template-columns: 56px 1fr 56px; margin: 10px 0 14px; }
.stepper.small button { height: 52px; font-size: 26px; border-radius: 14px; }
.stepper.small strong { font-size: 36px; }
.send-error { color: #991b1b; font-size: 13px; margin: 0 0 10px; }
.manager-hint { margin: 8px 18px 0; font-size: 13px; color: var(--ink-3); }
.issues { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }
.issue { display: flex; flex-direction: column; align-items: center; gap: 4px; padding: 12px 4px; background: #f8fafc; color: var(--ink-2); border: 1px solid var(--line); font-size: 13px; }
.issue span { font-size: 20px; }
.issue:hover:not(:disabled) { background: #f1f5f9; }
.issue.on { background: var(--overflow-soft); border-color: var(--overflow); color: #92400e; }
.memo { width: 100%; margin-top: 10px; padding: 14px; font-size: 16px; }

.toast { position: fixed; bottom: 28px; left: 50%; transform: translateX(-50%); background: var(--ink); color: #fff; padding: 12px 18px; border-radius: 999px; font-size: 14px; white-space: nowrap; z-index: 30; }
.toast-enter-active, .toast-leave-active, .sheet-enter-active, .sheet-leave-active { transition: all 0.2s; }
.toast-enter-from, .toast-leave-to { opacity: 0; transform: translate(-50%, 8px); }
.sheet-enter-from, .sheet-leave-to { opacity: 0; }
.sheet-enter-from .sheet, .sheet-leave-to .sheet { transform: translateY(40px); }
</style>
