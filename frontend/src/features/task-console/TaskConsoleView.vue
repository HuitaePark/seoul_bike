<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { useTaskStream } from '../../shared/composables/useTaskStream'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'

// 관제 작업 화면 (ROLES 1): 요청 입력, 작업 목록, 진행 이벤트 로그, 승인 카드.
// agent가 붙어 있으면 근무조 그래프가, 없으면 backend의 가짜 에이전트(FAKE_AGENT_ENABLED)가 답한다.

type TaskType = Schemas['TaskType']
type Shift = 'AM' | 'PM' | 'EVE'
type Region = { id: string; name: string; description: string; available: boolean }

// 현장 보고 재경로(FIELD_REPORT)는 기사가 POST /api/reports로 보고할 때만 생긴다. 작업 API로 만들면 400 USE_REPORTS_API
const types: { value: TaskType; label: string; hint: string }[] = [
  { value: 'PLAN_DRAFT', label: '계획 초안', hint: '새 경로' },
  { value: 'REQUEST', label: '상황 반영', hint: '제약 추가' },
  { value: 'QUESTION', label: '질의', hint: '근거 답변' },
]
const today = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' })
const shifts: { value: Shift; label: string; time: string }[] = [
  { value: 'AM', label: '오전', time: '07:00' },
  { value: 'PM', label: '오후', time: '13:00' },
  { value: 'EVE', label: '저녁', time: '19:00' },
]
const regions: Region[] = [
  { id: 'gwangjin-seongdong', name: '광진·성동', description: '통합 권역', available: true },
  { id: 'gangnam-seocho', name: '강남·서초', description: '준비 중', available: false },
  { id: 'mapo-seodaemun', name: '마포·서대문', description: '준비 중', available: false },
  { id: 'yeongdeungpo-dongjak', name: '영등포·동작', description: '준비 중', available: false },
]
const form = ref({ type: 'PLAN_DRAFT' as TaskType, regionId: regions[0]!.id, date: today, shift: 'AM' as Shift, text: '' })
const regionOpen = ref(false)
const regionQuery = ref('')
const regionField = ref<HTMLElement | null>(null)
const selectedRegion = computed(() => regions.find((r) => r.id === form.value.regionId) ?? regions[0]!)
const filteredRegions = computed(() => {
  const q = regionQuery.value.trim().toLocaleLowerCase('ko-KR')
  return q ? regions.filter((r) => `${r.name} ${r.id}`.toLocaleLowerCase('ko-KR').includes(q)) : regions
})
const shiftId = computed(() => `${form.value.regionId}:${form.value.date}:${form.value.shift}`)

function chooseRegion(region: Region) {
  if (!region.available) return
  form.value.regionId = region.id
  regionOpen.value = false
  regionQuery.value = ''
}

function closeRegionOnOutside(event: PointerEvent) {
  if (!regionOpen.value || regionField.value?.contains(event.target as Node)) return
  regionOpen.value = false
  regionQuery.value = ''
}
const formError = ref('')
const tasks = ref<Schemas['Task'][]>([])
const selected = ref<string | null>(null)
const decisionError = ref('')
const deciding = ref(false)

const stream = useTaskStream(selected)

// 진행 이벤트 로그. TOKEN은 답변으로 모이므로 로그에서 뺀다.
const log = computed(() =>
  stream.events.value
    .filter((e) => e.type !== 'TOKEN')
    .map((e) => ({
      seq: e.seq,
      type: e.type,
      text:
        e.type === 'TOOL_START' || e.type === 'TOOL_END'
          ? `${e.tool?.label ?? e.tool?.name ?? ''}${e.type === 'TOOL_END' ? (e.tool?.ok ? ' ✓' : ' ✗') : ''}`
          : e.type === 'APPROVAL_REQUIRED'
            ? `승인 요청: ${e.approval?.summary ?? ''}`
            : e.type === 'FAILED'
              ? (e.error ?? '실패')
              : '완료',
    })),
)

async function refresh() {
  const { data } = await api.GET('/api/ai/tasks', { params: { query: { size: 20 } } })
  tasks.value = data?.items ?? []
}

async function create() {
  formError.value = ''
  const { data, error } = await api.POST('/api/ai/tasks', {
    body: { type: form.value.type, shiftId: shiftId.value, text: form.value.text || undefined },
  })
  if (error || !data) {
    formError.value = problemMessage(error)
    return
  }
  selected.value = data.taskId
  await refresh()
}

async function decide(body: ApprovalDecision) {
  if (!selected.value) return
  deciding.value = true
  decisionError.value = ''
  const { error } = await api.POST('/api/ai/tasks/{taskId}/approval', {
    params: { path: { taskId: selected.value } },
    body,
  })
  deciding.value = false
  if (error) decisionError.value = problemMessage(error)
}

// 다른 작업을 고르면 지난 결정 오류를 지운다
watch(selected, () => (decisionError.value = ''))

watch(stream.state, (s) => {
  if (s === 'done') refresh()
})
onMounted(refresh)
onMounted(() => document.addEventListener('pointerdown', closeRegionOnOutside))
onBeforeUnmount(() => document.removeEventListener('pointerdown', closeRegionOnOutside))

const statusLabel: Record<Schemas['TaskStatus'], string> = {
  QUEUED: '대기',
  RUNNING: '진행 중',
  WAITING_APPROVAL: '승인 대기',
  DONE: '완료',
  FAILED: '실패',
}
</script>

<template>
  <div class="console">
    <section class="card">
      <h2>작업 만들기</h2>
      <form @submit.prevent="create">
        <fieldset class="choice-field">
          <legend>종류</legend>
          <div class="choice-options task-options">
            <label v-for="t in types" :key="t.value" :class="{ active: form.type === t.value }">
              <input v-model="form.type" type="radio" name="task-type" :value="t.value" />
              <strong>{{ t.label }}</strong>
              <span>{{ t.hint }}</span>
            </label>
          </div>
        </fieldset>
        <div class="context-grid">
          <div ref="regionField" class="region-field">
            <span class="field-label">권역</span>
            <button class="region-trigger" type="button" :aria-expanded="regionOpen" @click="regionOpen = !regionOpen">
              <span class="region-mark" aria-hidden="true">{{ selectedRegion.name.slice(-1) }}</span>
              <span class="region-copy"><strong>{{ selectedRegion.name }}</strong><small>{{ selectedRegion.description }}</small></span>
              <span class="region-chevron" :class="{ open: regionOpen }">⌄</span>
            </button>
            <div v-if="regionOpen" class="region-popover">
              <div class="region-search">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" aria-hidden="true">
                  <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
                </svg>
                <input v-model="regionQuery" placeholder="권역 검색" aria-label="권역 검색" @keydown.esc="regionOpen = false" />
              </div>
              <div class="region-results">
                <button
                  v-for="r in filteredRegions"
                  :key="r.id"
                  type="button"
                  :disabled="!r.available"
                  :class="{ selected: r.id === form.regionId }"
                  @click="chooseRegion(r)"
                >
                  <span class="region-mark" aria-hidden="true">{{ r.name.slice(-1) }}</span>
                  <span class="region-copy"><strong>{{ r.name }}</strong><small>{{ r.id }}</small></span>
                  <span v-if="r.id === form.regionId" class="region-check">✓</span>
                  <span v-else-if="!r.available" class="soon">준비 중</span>
                </button>
                <p v-if="!filteredRegions.length" class="muted">일치하는 권역이 없다</p>
              </div>
            </div>
          </div>
          <label>
            날짜
            <input v-model="form.date" type="date" required />
          </label>
        </div>
        <fieldset class="choice-field">
          <legend>근무조</legend>
          <div class="choice-options shift-options">
            <label v-for="s in shifts" :key="s.value" :class="{ active: form.shift === s.value }">
              <input v-model="form.shift" type="radio" name="shift" :value="s.value" />
              <strong>{{ s.label }}</strong>
              <span>{{ s.time }}</span>
            </label>
          </div>
        </fieldset>
        <label>요청·질문 <textarea v-model="form.text" rows="3" placeholder="예: 여의도 불꽃축제로 대여·반납 중지" /></label>
        <p v-if="formError" class="error">{{ formError }}</p>
        <button>작업 접수</button>
      </form>

      <h2>작업 목록 <button class="ghost" @click="refresh">새로고침</button></h2>
      <ul class="tasks">
        <li
          v-for="t in tasks"
          :key="t.taskId"
          :class="{ active: t.taskId === selected }"
          @click="selected = t.taskId"
        >
          <span :class="['badge', t.status]">{{ statusLabel[t.status] }}</span>
          {{ t.type }} · {{ new Date(t.createdAt).toLocaleTimeString('ko-KR') }}
        </li>
        <li v-if="tasks.length === 0" class="muted">아직 작업이 없다</li>
      </ul>
    </section>

    <section class="card">
      <h2>진행 상황</h2>
      <p v-if="!selected" class="muted">작업을 만들거나 목록에서 고른다</p>
      <template v-else>
        <p class="muted">스트림: {{ stream.state.value }}</p>
        <p v-if="stream.activeTool.value" class="tool">⏳ {{ stream.activeTool.value.label }}</p>
        <p class="answer">{{ stream.answer.value }}</p>

        <ApprovalCard
          v-if="stream.pendingApproval.value"
          :card="stream.pendingApproval.value"
          :busy="deciding"
          :error="decisionError"
          @decide="decide"
        />

        <p v-if="stream.error.value" class="error">{{ stream.error.value }}</p>
        <div v-if="stream.sources.value.length" class="sources">
          <h3>근거</h3>
          <ul>
            <li v-for="s in stream.sources.value" :key="s.title">{{ s.title }} <span class="muted">{{ s.uri }}</span></li>
          </ul>
        </div>

        <details v-if="log.length" class="log" open>
          <summary>진행 이벤트 {{ log.length }}건</summary>
          <ol>
            <li v-for="e in log" :key="e.seq">
              <span :class="['badge', e.type === 'DONE' ? 'DONE' : e.type === 'FAILED' ? 'FAILED' : '']">{{ e.type }}</span>
              {{ e.text }}
            </li>
          </ol>
        </details>
      </template>
    </section>
  </div>
</template>

<style scoped>
.context-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; align-items: end; }
.context-grid > label, .region-field { min-width: 0; }
.field-label { display: block; margin-bottom: 4px; font-size: 13px; color: var(--ink-2); font-weight: 500; }
.region-field { position: relative; }
.region-trigger { width: 100%; height: 48px; display: flex; align-items: center; gap: 8px; padding: 7px 10px; border: 1px solid var(--line-2); border-radius: 10px; background: #f8fafc; color: var(--ink); text-align: left; font-weight: inherit; }
.region-trigger:hover:not(:disabled) { border-color: var(--brand); background: var(--brand-soft); }
.region-mark { width: 26px; height: 26px; flex: 0 0 auto; display: grid; place-items: center; border-radius: 8px; background: var(--brand-soft); color: var(--brand-strong); font-size: 12px; font-weight: 800; }
.region-copy { min-width: 0; display: flex; flex-direction: column; line-height: 1.2; }
.region-copy small { color: var(--ink-3); font-size: 10px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
.region-chevron { margin-left: auto; color: var(--ink-3); font-size: 18px; line-height: 1; transition: transform 0.15s; }
.region-chevron.open { transform: rotate(180deg); }
.context-grid input[type='date'] { width: 100%; height: 48px; min-height: 48px; margin: 0; }
.region-popover { position: absolute; top: calc(100% + 6px); left: 0; width: min(380px, calc(100vw - 64px)); z-index: 8; padding: 8px; border: 1px solid var(--line); border-radius: 12px; background: #fff; box-shadow: 0 12px 30px rgb(15 23 42 / 0.16); font-size: 10px; }
.region-search { display: flex; align-items: center; gap: 6px; padding: 0 9px; border: 1px solid var(--line-2); border-radius: 9px; color: var(--ink-3); }
.region-search input { width: 100%; min-width: 0; height: 32px; padding: 0; border: 0; outline: 0; font-size: 11px; }
.region-results { display: flex; flex-direction: column; gap: 3px; margin-top: 6px; }
.region-results button { width: 100%; display: flex; align-items: center; gap: 8px; padding: 6px 8px; border-radius: 9px; background: #fff; color: var(--ink); text-align: left; font-weight: inherit; }
.region-results .region-copy { flex: 1; }
.region-results .region-copy strong { font-size: 11px; white-space: nowrap; }
.region-results .region-copy small { font-size: 8px; white-space: nowrap; }
.soon { white-space: nowrap; }
.region-results button:hover:not(:disabled), .region-results button.selected { background: var(--brand-soft); }
.region-results button:disabled { opacity: 0.52; }
.region-check { margin-left: auto; color: var(--brand-strong); font-weight: 800; }
.soon { margin-left: auto; padding: 2px 6px; border-radius: 999px; background: #eef2f6; color: var(--ink-3); font-size: 8px; }
.region-results .muted { margin: 10px 4px; text-align: center; }
.choice-field { border: 0; padding: 0; margin: 0; min-width: 0; }
.choice-field legend { margin-bottom: 4px; font-size: 13px; color: var(--ink-2); font-weight: 500; }
.choice-options { display: grid; grid-template-columns: repeat(3, 1fr); gap: 6px; }
.choice-options label { position: relative; display: flex; align-items: center; gap: 1px; padding: 8px 4px; border: 1px solid var(--line); border-radius: 10px; background: #fff; cursor: pointer; text-align: center; transition: border-color 0.15s, background 0.15s, box-shadow 0.15s; }
.choice-options label:hover { border-color: var(--brand); background: var(--brand-soft); }
.choice-options label.active { border-color: var(--brand); background: var(--brand-soft); box-shadow: inset 0 0 0 1px var(--brand); color: var(--brand-strong); }
.choice-options input { position: absolute; opacity: 0; pointer-events: none; }
.choice-options strong { font-size: 13px; white-space: nowrap; }
.choice-options span { font-size: 11px; color: var(--ink-3); white-space: nowrap; }
.log { margin-top: 12px; font-size: 13px; }
.log ol { padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
</style>
