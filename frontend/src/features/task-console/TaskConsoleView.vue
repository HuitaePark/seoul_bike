<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { useTaskStream } from '../../shared/composables/useTaskStream'
import RegionPicker from '../../shared/region/RegionPicker.vue'
import { makeShiftId, SHIFT_SLOTS, todayInSeoul, type ShiftSlot } from '../../shared/region/regions'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'
import EvidencePanel from '../evidence/EvidencePanel.vue' // 유은님 추가 사항

// 관제 작업 화면 (ROLES 1): 요청 입력, 작업 목록, 진행 이벤트 로그, 승인 카드.
// agent가 붙어 있으면 근무조 그래프가, 없으면 backend의 가짜 에이전트(FAKE_AGENT_ENABLED)가 답한다.

type TaskType = Schemas['TaskType']

// 현장 보고 재경로(FIELD_REPORT)는 기사가 POST /api/reports로 보고할 때만 생긴다. 작업 API로 만들면 400 USE_REPORTS_API
const types: { value: TaskType; label: string }[] = [
  { value: 'PLAN_DRAFT', label: '계획 초안' },
  { value: 'REQUEST', label: '상황 반영 요청' },
  { value: 'QUESTION', label: '질의' },
]
// 근무조는 권역·날짜·시간대를 따로 골라 근무조 ID(권역:날짜:시간대)로 합친다
const form = ref({ type: 'PLAN_DRAFT' as TaskType, regionId: 'N02', date: todayInSeoul(), slot: 'AM' as ShiftSlot, text: '' })
const shiftId = computed(() => makeShiftId(form.value.regionId, form.value.date, form.value.slot))
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
        <label>
          종류
          <select v-model="form.type">
            <option v-for="t in types" :key="t.value" :value="t.value">{{ t.label }}</option>
          </select>
        </label>
        <fieldset class="shift">
          <legend>근무조</legend>
          <RegionPicker v-model="form.regionId" label="권역" required />
          <div class="shift-row">
            <label>날짜 <input v-model="form.date" type="date" required /></label>
            <label>
              시간대
              <select v-model="form.slot">
                <option v-for="s in SHIFT_SLOTS" :key="s.id" :value="s.id">{{ s.label }} {{ s.time }}</option>
              </select>
            </label>
          </div>
          <span class="hint">근무조 ID <code>{{ shiftId }}</code></span>
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
        <!-- <div v-if="stream.sources.value.length" class="sources">
          <h3>근거</h3>
          <ul>
            <li v-for="s in stream.sources.value" :key="s.title">{{ s.title }} <span class="muted">{{ s.uri }}</span></li>
          </ul>
        </div> -->
        <EvidencePanel v-if="stream.state.value === 'done'" :sources="stream.sources.value" />

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
.shift { display: flex; flex-direction: column; gap: 8px; margin: 0; padding: 10px 12px 12px; border: 1px solid var(--line); border-radius: 12px; min-width: 0; }
.shift legend { padding: 0 4px; font-size: 13px; font-weight: 600; color: var(--ink-2); }
.shift-row { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 8px; }
.shift-row input, .shift-row select { width: 100%; min-width: 0; }
.shift code { font-size: 11.5px; color: var(--ink-2); overflow-wrap: anywhere; }
.log { margin-top: 12px; font-size: 13px; }
.log ol { padding-left: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
</style>
