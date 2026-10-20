<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { useTaskStream } from '../../shared/composables/useTaskStream'

// 작업 콘솔: 공통 뼈대(api 클라이언트, useTaskStream) 사용 예시이자 에이전트 화면의 출발점.
// 진짜 agent가 붙기 전에는 backend의 가짜 에이전트가 답한다.

type TaskType = Schemas['TaskType']

const types: { value: TaskType; label: string }[] = [
  { value: 'PLAN_DRAFT', label: '계획 초안' },
  { value: 'REQUEST', label: '상황 반영 요청' },
  { value: 'FIELD_REPORT', label: '현장 보고 재경로' },
  { value: 'QUESTION', label: '질의' },
]
const today = new Date().toISOString().slice(0, 10)
const form = ref({ type: 'PLAN_DRAFT' as TaskType, shiftId: `gwangjin-seongdong:${today}:AM`, text: '' })
const formError = ref('')
const tasks = ref<Schemas['Task'][]>([])
const selected = ref<string | null>(null)
const decisionError = ref('')
const deciding = ref(false)

const stream = useTaskStream(selected)

async function refresh() {
  const { data } = await api.GET('/api/ai/tasks', { params: { query: { size: 20 } } })
  tasks.value = data?.items ?? []
}

async function create() {
  formError.value = ''
  const { data, error } = await api.POST('/api/ai/tasks', {
    body: { type: form.value.type, shiftId: form.value.shiftId, text: form.value.text || undefined },
  })
  if (error || !data) {
    formError.value = problemMessage(error)
    return
  }
  selected.value = data.taskId
  await refresh()
}

async function decide(approvalId: string, decision: 'APPROVE' | 'REJECT') {
  if (!selected.value) return
  deciding.value = true
  decisionError.value = ''
  const { error } = await api.POST('/api/ai/tasks/{taskId}/approval', {
    params: { path: { taskId: selected.value } },
    body: { approvalId, decision },
  })
  deciding.value = false
  if (error) decisionError.value = problemMessage(error)
}

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
        <label>근무조 <input v-model="form.shiftId" required /></label>
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

        <div v-if="stream.pendingApproval.value" class="approval">
          <h3>{{ stream.pendingApproval.value.summary }}</h3>
          <p class="muted">
            승인 주체: {{ stream.pendingApproval.value.approver }}
            <template v-if="stream.pendingApproval.value.truckId"> · {{ stream.pendingApproval.value.truckId }}</template>
            <template v-if="stream.pendingApproval.value.grade"> · {{ stream.pendingApproval.value.grade }}</template>
          </p>
          <ul>
            <li v-for="r in stream.pendingApproval.value.reasons" :key="r">{{ r }}</li>
          </ul>
          <p v-if="decisionError" class="error">{{ decisionError }}</p>
          <button :disabled="deciding" @click="decide(stream.pendingApproval.value.approvalId, 'APPROVE')">승인</button>
          <button
            class="ghost"
            :disabled="deciding"
            @click="decide(stream.pendingApproval.value.approvalId, 'REJECT')"
          >
            거절
          </button>
        </div>

        <p v-if="stream.error.value" class="error">{{ stream.error.value }}</p>
        <div v-if="stream.sources.value.length" class="sources">
          <h3>근거</h3>
          <ul>
            <li v-for="s in stream.sources.value" :key="s.title">{{ s.title }} <span class="muted">{{ s.uri }}</span></li>
          </ul>
        </div>
      </template>
    </section>
  </div>
</template>
