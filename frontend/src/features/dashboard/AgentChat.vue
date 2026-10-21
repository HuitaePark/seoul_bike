<!--
에이전트 대화. 에이전트가 올린 계획 요약과 변경안(승인 카드)을 대화처럼 보여 주고,
관리자의 결정과 질문도 같은 흐름에 남긴다. 계획을 새로 만드는 일은 작업 콘솔(작업 요청)에서 한다.
-->
<script setup lang="ts">
import { nextTick, ref } from 'vue'
import type { Schemas } from '../../shared/api/client'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'
import { kindLabel } from '../approval/approval'

type Pending = Schemas['ApprovalRequest'] & { createdMin: number }

defineProps<{ pending: Pending[]; summary: string; version: number }>()
const emit = defineEmits<{ decide: [body: ApprovalDecision, summary: string] }>()

const open = ref<string | null>(null)
const question = ref('')
const log = ref<{ who: 'me' | 'agent'; text: string }[]>([])
const body = ref<HTMLElement | null>(null)

async function push(...entries: { who: 'me' | 'agent'; text: string }[]) {
  log.value.push(...entries)
  await nextTick()
  body.value?.scrollTo({ top: body.value.scrollHeight, behavior: 'smooth' })
}

function decide(d: ApprovalDecision, p: Pending) {
  open.value = null
  emit('decide', d, p.summary)
  const verb = d.decision === 'APPROVE' ? '승인' : '거절'
  push({ who: 'me', text: `「${p.summary}」 ${verb}` }, { who: 'agent', text: `${verb} 결과를 반영했다 (더미).` })
}

// 더미: 정해 둔 답을 보여 준다. 실제로는 QUESTION 작업을 만들고 SSE로 답을 받는다.
function ask() {
  const q = question.value.trim()
  if (!q) return
  question.value = ''
  push(
    { who: 'me', text: q },
    {
      who: 'agent',
      text: '서울숲 정문(3시간 뒤 34대)과 뚝섬유원지역(31대)이 거치대 수를 넘을 것으로 예측돼 회수가 많다. 두 곳 모두 고정 회수 거점이다.',
    },
  )
}
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
        <div :class="['bubble', 'proposal', { major: p.grade === 'MAJOR' }]">
          <span class="meta">
            <span class="badge WAITING_APPROVAL">{{ kindLabel[p.kind] }}</span>
            <span v-if="p.grade === 'MAJOR'" class="badge FAILED">큰 변경</span>
            {{ p.createdMin }}분 전<template v-if="p.truckId"> · {{ p.truckId }}</template>
          </span>
          <strong>{{ p.summary }}</strong>
          <button v-if="open !== p.approvalId" class="review" @click="open = p.approvalId">검토하고 결정하기</button>
          <template v-else>
            <ApprovalCard :card="p" @decide="decide($event, p)" />
            <button class="ghost close" @click="open = null">접기</button>
          </template>
        </div>
      </div>

      <div v-for="(m, i) in log" :key="i" :class="['msg', m.who]">
        <span v-if="m.who === 'agent'" class="who" aria-hidden="true">AI</span>
        <div class="bubble">{{ m.text }}</div>
      </div>
    </div>

    <form class="input" @submit.prevent="ask">
      <input v-model="question" placeholder="이 화면에 대해 묻기 · 예) 성수 쪽 회수가 왜 많지?" aria-label="에이전트에게 물을 내용" />
      <button aria-label="보내기">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12 20 4l-6 16-3-7-7-1z" /></svg>
      </button>
    </form>
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
.proposal .meta { color: var(--ink-3); font-weight: 600; }
.proposal strong { color: var(--ink); font-size: 13.5px; }
.review { align-self: flex-start; margin-top: 4px; padding: 6px 12px; font-size: 12px; }
.close { align-self: flex-end; padding: 4px 10px; font-size: 12px; }
.proposal :deep(.approval) { margin: 6px 0 0; padding: 12px; border-width: 1px; }
.input { flex-direction: row; gap: 6px; margin: 0; padding: 6px; border: 1px solid var(--line-2); border-radius: 12px; background: #fff; }
.input:focus-within { border-color: var(--brand); }
.input input { flex: 1; min-width: 0; border: 0; padding: 6px 8px; }
.input input:focus { outline: none; }
.input button { width: 36px; height: 36px; padding: 0; display: grid; place-items: center; border-radius: 9px; }
</style>
