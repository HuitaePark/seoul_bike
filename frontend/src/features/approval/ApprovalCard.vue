<!--
Author: Jung Minkyu
Date: 20OCT2026
Purpose: 승인 카드 (openapi ApprovalRequest 하나). 승인·거절·일부 제외를 ApprovalDecision으로 보낸다. 관리자 화면과 기사 화면이 같이 쓴다.
누가 결정할 수 있는지는 backend가 판단한다. 버튼은 늘 보이고, 승인 주체가 아니면 403 NOT_APPROVER가 error로 온다.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { ApprovalCard, ApprovalDecision } from './approval'
import StopList from './StopList.vue'
import { approvalSteps, approverLabel, decisionBody, diffMarks, gradeLabel, kindLabel, stationChoices } from './approval'

const props = withDefaults(
  defineProps<{
    card: ApprovalCard
    busy?: boolean // 보낸 뒤 응답 전이면 버튼을 막는다
    error?: string // 보내기 실패 사유 (409: 이미 처리·대체됨, 403: 승인 주체 아님)
  }>(),
  { busy: false, error: '' },
)
// POST /api/ai/tasks/{taskId}/approval 몸통. 보내는 일은 쓰는 화면이 한다.
const emit = defineEmits<{ decide: [body: ApprovalDecision] }>()

const mode = ref<'' | 'exclude' | 'reject'>('')
const picked = ref<string[]>([])
const comment = ref('')

// 새 카드가 오면(다시 계산) 고르던 것을 비운다
watch(
  () => props.card.approvalId,
  () => {
    mode.value = ''
    picked.value = []
    comment.value = ''
  },
)

const choices = computed(() => stationChoices(props.card))
const marks = computed(() => diffMarks(props.card))
const steps = computed(() => approvalSteps(props.card))

function toggle(id: string) {
  picked.value = picked.value.includes(id) ? picked.value.filter((x) => x !== id) : [...picked.value, id]
}

function send(decision: ApprovalDecision['decision'], exclude: string[] = []) {
  emit('decide', decisionBody(props.card, decision, { exclude, comment: comment.value }))
}
</script>

<template>
  <section class="approval" :aria-label="`승인 카드: ${card.summary}`">
    <header>
      <span class="badge">{{ kindLabel[card.kind] }}</span>
      <span class="badge WAITING_APPROVAL">{{ approverLabel[card.approver] }}</span>
      <span v-if="card.grade" class="badge">{{ gradeLabel[card.grade] }}</span>
      <span v-if="card.truckId" class="badge">{{ card.truckId }}</span>
    </header>
    <h3>{{ card.summary }}</h3>

    <ol v-if="steps.length" class="steps" aria-label="승인 단계">
      <li v-for="s in steps" :key="s.role" :class="s.state">
        {{ s.label }}<template v-if="s.state === 'done'"> ✓</template>
      </li>
    </ol>

    <ul v-if="card.reasons?.length" class="reasons">
      <li v-for="r in card.reasons" :key="r">{{ r }}</li>
    </ul>

    <div v-if="card.diff" class="diff">
      <div>
        <h4>지금 경로</h4>
        <StopList :stops="card.diff.before" :marks="marks" />
      </div>
      <div>
        <h4>바뀐 경로</h4>
        <StopList :stops="card.diff.after" :marks="marks" />
      </div>
    </div>

    <template v-if="card.routes?.length">
      <h4>트럭별 경로 <span v-if="mode === 'exclude'" class="muted">— 뺄 대여소를 누른다</span></h4>
      <div v-for="r in card.routes" :key="r.truckId" class="route">
        <span class="truck">{{ r.truckId }}</span>
        <StopList :stops="r.stops" :selectable="mode === 'exclude'" :selected="picked" @toggle="toggle" />
      </div>
    </template>

    <p v-if="error" class="error" role="alert">{{ error }}</p>

    <footer class="actions">
      <template v-if="mode === ''">
        <button :disabled="busy" @click="send('APPROVE')">{{ card.approver === 'DRIVER' ? '수락' : '승인' }}</button>
        <button v-if="card.kind === 'SUBMIT_PLAN'" class="ghost" :disabled="busy" @click="mode = 'exclude'">일부 제외</button>
        <button class="ghost danger" :disabled="busy" @click="mode = 'reject'">거절</button>
      </template>

      <template v-else-if="mode === 'exclude'">
        <div v-if="!card.routes?.length" class="choices">
          <button
            v-for="id in choices"
            :key="id"
            type="button"
            :class="['ghost', { picked: picked.includes(id) }]"
            :aria-pressed="picked.includes(id)"
            @click="toggle(id)"
          >
            {{ id }}
          </button>
        </div>
        <button :disabled="busy || !picked.length" @click="send('REJECT', picked)">
          {{ picked.length }}곳 빼고 다시 계산
        </button>
        <button class="ghost" :disabled="busy" @click="mode = ''">취소</button>
      </template>

      <form v-else class="reject" @submit.prevent="send('REJECT')">
        <label>
          거절 사유 (선택)
          <textarea v-model="comment" rows="2" maxlength="500" />
        </label>
        <div>
          <button class="danger" type="submit" :disabled="busy">거절</button>
          <button class="ghost" type="button" :disabled="busy" @click="mode = ''">취소</button>
        </div>
      </form>
    </footer>
  </section>
</template>

<style scoped>
h3 { margin: 4px 0; }
h4 { margin: 8px 0 2px; font-size: 13px; color: #374151; }
.steps { list-style: none; display: flex; gap: 6px; padding: 0; margin: 0; font-size: 12px; }
.steps li { padding: 2px 8px; border-radius: 999px; border: 1px solid #d1d5db; color: #6b7280; }
.steps li + li::before { content: '→ '; }
.steps li.done { background: #dcfce7; border-color: #86efac; color: #166534; }
.steps li.current { background: #fef3c7; border-color: #f59e0b; color: #92400e; font-weight: 600; }
.reasons { margin: 0; padding-left: 18px; font-size: 14px; }
.diff { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; width: 100%; }
.route { display: flex; gap: 8px; align-items: baseline; }
.truck { font-weight: 600; font-size: 13px; min-width: 56px; }
.actions { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; width: 100%; }
.choices { display: flex; flex-wrap: wrap; gap: 6px; width: 100%; }
.choices .picked { background: #fee2e2; text-decoration: line-through; }
.reject { width: 100%; margin: 0; }
.danger { background: #b91c1c; }
.ghost.danger { background: transparent; color: #b91c1c; border: 1px solid #fca5a5; }
@media (max-width: 640px) {
  .diff { grid-template-columns: 1fr; }
}
</style>
