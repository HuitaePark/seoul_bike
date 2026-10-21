<!--
에이전트 대화. 근무조 작업의 승인 요청(승인 카드)과 묻기·결정의 결과를 대화처럼 보여 준다 (useShiftFeed가 SSE로 채운다).
질문은 같은 에이전트 화면의 묻기 줄(AskBar)에서 하고, 계획을 새로 만드는 일은 작업 콘솔에서 한다.
변경안을 펼치거나 '지도에서 보기'를 누르면 위 지도에 그 변경안만 겹쳐 보인다 (preview).
-->
<script setup lang="ts">
import { nextTick, onScopeDispose, ref, watch } from 'vue'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'
import { kindLabel } from '../approval/approval'
import { minutesAgo, type Line, type LiveCard } from './feed'

const props = withDefaults(
  defineProps<{
    pending: LiveCard[]
    summary: string
    version: number
    log: Line[]
    previewing?: string | null
    /** 결정을 보내는 중인 승인 요청 */
    busy?: string | null
    errors?: Record<string, string>
    visible?: boolean
  }>(),
  { previewing: null, busy: null, errors: () => ({}), visible: true },
)
const emit = defineEmits<{ decide: [body: ApprovalDecision, card: LiveCard]; preview: [card: LiveCard | null, scroll: boolean] }>()

const open = ref<string | null>(null)
const body = ref<HTMLElement | null>(null)
// "몇 분 전"을 30초마다 다시 센다
const now = ref(Date.now())
const tick = window.setInterval(() => (now.value = Date.now()), 30_000)
onScopeDispose(() => window.clearInterval(tick))

function review(p: LiveCard) {
  open.value = p.approvalId
  emit('preview', p, false)
}

function fold() {
  open.value = null
  emit('preview', null, false)
}

// 결정이 받아들여지거나 다른 곳에서 처리돼 카드가 내려가면 펼친 것도 접는다
watch(
  () => props.pending.map((p) => p.approvalId),
  (ids) => {
    if (open.value && !ids.includes(open.value)) open.value = null
  },
)

// 새 줄이 붙거나 답이 이어지면 맨 아래로
watch(
  () => [props.log.length, props.log.at(-1)?.text],
  async () => {
    if (!props.visible) return
    await nextTick()
    body.value?.scrollTo({ top: body.value.scrollHeight, behavior: 'smooth' })
  },
)
watch(() => props.visible, async (visible) => {
  if (!visible) return
  await nextTick()
  body.value?.scrollTo({ top: body.value.scrollHeight })
})
</script>

<template>
  <div class="chat">
    <div ref="body" class="body">
      <div class="msg agent">
        <span class="who" aria-hidden="true">AI</span>
        <div class="bubble">
          <span class="meta">계획 v{{ version }} 요약</span>
          {{ summary }}
        </div>
      </div>

      <div v-for="p in pending" :key="p.approvalId" class="msg agent">
        <span class="who" aria-hidden="true">AI</span>
        <div :class="['bubble', 'proposal', { major: p.grade === 'MAJOR', on: previewing === p.approvalId }]">
          <span class="meta">
            <span class="badge WAITING_APPROVAL">{{ kindLabel[p.kind] }}</span>
            <span v-if="p.grade === 'MAJOR'" class="badge FAILED">큰 변경</span>
            {{ minutesAgo(p.since, now) }}분 전<template v-if="p.truckId"> · {{ p.truckId }}</template>
          </span>
          <strong>{{ p.summary }}</strong>
          <div v-if="open !== p.approvalId" class="row">
            <button class="review" @click="review(p)">검토하고 결정하기</button>
            <button class="ghost review" @click="emit('preview', p, true)">지도에서 보기</button>
          </div>
          <template v-else>
            <button class="ghost review" @click="emit('preview', p, true)">지도에서 바뀐 경로 보기</button>
            <ApprovalCard :card="p" :busy="busy === p.approvalId" :error="errors[p.approvalId] ?? ''" @decide="emit('decide', $event, p)" />
            <button class="ghost close" @click="fold">접기</button>
          </template>
        </div>
      </div>

      <div v-for="(m, i) in log" :key="i" :class="['msg', m.who, m.state]">
        <span v-if="m.who === 'agent'" class="who" aria-hidden="true">AI</span>
        <div class="bubble" :aria-busy="m.state === 'working'">
          <span v-if="m.text" class="text">{{ m.text }}</span>
          <span v-if="m.state === 'working'" class="note">{{ m.tool ? `${m.tool}…` : m.text ? '' : '답을 만드는 중…' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.chat { display: flex; flex-direction: column; gap: 10px; min-height: 0; flex: 1; }
.body { flex: 1; min-height: 0; max-height: 360px; overflow-y: auto; display: flex; flex-direction: column; gap: 10px; padding-right: 4px; }
.msg { display: flex; gap: 8px; align-items: flex-start; max-width: 100%; }
.msg.me { justify-content: flex-end; }
.who { flex: none; width: 26px; height: 26px; border-radius: 8px; background: var(--brand); color: #fff; font-size: 10px; font-weight: 800; display: grid; place-items: center; }
.bubble { display: flex; flex-direction: column; gap: 4px; padding: 9px 12px; border-radius: 4px 12px 12px 12px; background: #f1f5f9; font-size: 13px; line-height: 1.5; color: var(--ink-2); min-width: 0; }
.msg.me .bubble { border-radius: 12px 4px 12px 12px; background: var(--brand); color: #fff; max-width: 80%; }
.meta { display: flex; align-items: center; gap: 4px; flex-wrap: wrap; font-size: 11px; font-weight: 700; color: var(--brand-strong); }
.meta .badge { margin: 0; }
.proposal { background: #fff; border: 1px solid var(--line); border-left: 4px solid var(--overflow); width: 100%; }
.proposal.major { border-left-color: var(--empty); }
.proposal.on { box-shadow: 0 0 0 2px #fdba74; }
.proposal .meta { color: var(--ink-3); font-weight: 600; }
.proposal strong { color: var(--ink); font-size: 13.5px; }
.row { display: flex; flex-wrap: wrap; gap: 6px; }
.review { align-self: flex-start; margin-top: 4px; padding: 6px 12px; font-size: 12px; }
.close { align-self: flex-end; padding: 4px 10px; font-size: 12px; }
.text { white-space: pre-wrap; }
.note { font-size: 11.5px; color: var(--ink-3); }
.note:empty { display: none; }
.msg.failed .bubble { background: #fef2f2; color: #991b1b; }
.proposal :deep(.approval) { margin: 6px 0 0; padding: 12px; border-width: 1px; }
</style>
