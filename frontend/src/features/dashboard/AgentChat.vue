<!--
에이전트 대화. 에이전트가 올린 계획 요약과 변경안(승인 카드)을 대화처럼 보여 주고,
관리자의 결정과 질문도 같은 흐름에 남긴다. 질문은 대시보드 맨 위 묻기 줄(AskBar)에서 하고, 계획을 새로 만드는 일은 작업 콘솔에서 한다.
변경안을 펼치거나 '지도에서 보기'를 누르면 위 지도에 그 변경안만 겹쳐 보인다 (preview).
-->
<script setup lang="ts">
import { nextTick, ref, watch } from 'vue'
import type { Schemas } from '../../shared/api/client'
import ApprovalCard from '../approval/ApprovalCard.vue'
import type { ApprovalDecision } from '../approval/approval'
import { kindLabel } from '../approval/approval'

type Pending = Schemas['ApprovalRequest'] & { createdMin: number }

const props = defineProps<{ pending: Pending[]; summary: string; version: number; qa: { q: string; a: string }[]; previewing?: string | null }>()
const emit = defineEmits<{ decide: [body: ApprovalDecision, summary: string]; preview: [card: Pending | null, scroll: boolean] }>()

const open = ref<string | null>(null)
const log = ref<{ who: 'me' | 'agent'; text: string }[]>([])
const body = ref<HTMLElement | null>(null)

async function push(...entries: { who: 'me' | 'agent'; text: string }[]) {
  log.value.push(...entries)
  await nextTick()
  body.value?.scrollTo({ top: body.value.scrollHeight, behavior: 'smooth' })
}

function review(p: Pending) {
  open.value = p.approvalId
  emit('preview', p, false)
}

function fold() {
  open.value = null
  emit('preview', null, false)
}

function decide(d: ApprovalDecision, p: Pending) {
  open.value = null
  emit('decide', d, p.summary)
  const verb = d.decision === 'APPROVE' ? '승인' : '거절'
  push({ who: 'me', text: `「${p.summary}」 ${verb}` }, { who: 'agent', text: `${verb} 결과를 반영했다 (더미).` })
}

// 맨 위 묻기 줄에서 한 질문과 답을 대화 흐름에도 남긴다
watch(
  () => props.qa.length,
  (n, old) => {
    for (const { q, a } of props.qa.slice(old ?? 0, n)) push({ who: 'me', text: q }, { who: 'agent', text: a })
  },
)
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
            {{ p.createdMin }}분 전<template v-if="p.truckId"> · {{ p.truckId }}</template>
          </span>
          <strong>{{ p.summary }}</strong>
          <div v-if="open !== p.approvalId" class="row">
            <button class="review" @click="review(p)">검토하고 결정하기</button>
            <button class="ghost review" @click="emit('preview', p, true)">지도에서 보기</button>
          </div>
          <template v-else>
            <button class="ghost review" @click="emit('preview', p, true)">지도에서 바뀐 경로 보기</button>
            <ApprovalCard :card="p" @decide="decide($event, p)" />
            <button class="ghost close" @click="fold">접기</button>
          </template>
        </div>
      </div>

      <div v-for="(m, i) in log" :key="i" :class="['msg', m.who]">
        <span v-if="m.who === 'agent'" class="who" aria-hidden="true">AI</span>
        <div class="bubble">{{ m.text }}</div>
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
.proposal :deep(.approval) { margin: 6px 0 0; padding: 12px; border-width: 1px; }
</style>
