<!--
이 화면에 대해 묻기. 대시보드 맨 위 줄에 두어 어디서든 바로 묻는다 (질문·답은 에이전트 대화에도 남는다).
계획을 새로 만들거나 현장 상황을 반영하는 일은 묻기가 아니라 작업 콘솔에서 한다.
-->
<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{ qa: { q: string; a: string }[] }>()
const emit = defineEmits<{ ask: [question: string] }>()

const question = ref('')
const hidden = ref(-1) // 닫은 답의 순번. 새 질문을 하면 다시 보인다
const last = computed(() => (props.qa.length - 1 > hidden.value ? props.qa.at(-1) : undefined))

function submit() {
  const q = question.value.trim()
  if (!q) return
  question.value = ''
  emit('ask', q)
}
</script>

<template>
  <section class="askbar" aria-label="이 화면에 대해 묻기">
    <form @submit.prevent="submit">
      <span class="ai" aria-hidden="true">AI</span>
      <input v-model="question" placeholder="이 화면에 대해 묻기 · 예) 성수 쪽 회수가 왜 많지?" aria-label="에이전트에게 물을 내용" />
      <button>묻기</button>
    </form>
    <p class="hint">지금 계획과 지도에 대한 빠른 질문. 계획을 새로 만들거나 상황을 반영하려면 <RouterLink to="/console">작업 콘솔</RouterLink>을 쓴다.</p>
    <div v-if="last" class="answer" role="status">
      <span class="q">{{ last.q }}</span> <span class="dummy-tag">더미 답</span>
      <p>{{ last.a }}</p>
      <button class="ghost" aria-label="답 닫기" @click="hidden = qa.length - 1">닫기</button>
    </div>
  </section>
</template>

<style scoped>
.askbar { background: var(--surface); border: 1px solid var(--line); border-radius: var(--radius); box-shadow: var(--shadow); padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
form { flex-direction: row; align-items: center; gap: 8px; margin: 0; }
.ai { flex: none; width: 30px; height: 30px; border-radius: 9px; background: var(--brand); color: #fff; font-size: 11px; font-weight: 800; display: grid; place-items: center; }
input { flex: 1; min-width: 0; }
.hint { margin: 0 0 0 38px; }
.answer { position: relative; margin-left: 38px; padding: 10px 70px 10px 12px; border-radius: 4px 12px 12px 12px; background: #f1f5f9; font-size: 13px; line-height: 1.5; color: var(--ink-2); }
.answer .q { font-weight: 700; color: var(--ink); }
.answer p { margin: 2px 0 0; }
.answer button { position: absolute; top: 8px; right: 8px; padding: 3px 10px; font-size: 12px; }
@media (max-width: 640px) {
  .hint, .answer { margin-left: 0; }
}
</style>
