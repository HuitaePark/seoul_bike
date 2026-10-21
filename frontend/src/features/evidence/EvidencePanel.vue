<!--
Author: Jung Yueun
Date: 21OCT2026
Purpose: 근거 패널 (ROLES 3). 답이 어떤 문서와 업무 데이터에 기대는지 보여 준다.
         계약 openapi Source(kind, title, uri, excerpt)만 쓴다. 출처 등급(citation·basis)과 원문 보기는 계약에 들어온 뒤 붙인다.
사용: <EvidencePanel :sources="stream.sources.value" />
-->
<script setup lang="ts">
import { computed, useId } from 'vue'
import { groupSources, type Source } from './evidence'

const props = withDefaults(
  defineProps<{
    sources?: readonly Source[] | null
    /** 근거가 없을 때 안내를 보일지. 작업이 끝나기 전에는 false로 둔다 */
    showEmpty?: boolean
  }>(),
  { sources: () => [], showEmpty: true },
)

const grouped = computed(() => groupSources(props.sources))
const total = computed(() => grouped.value.docs.length + grouped.value.tools.length)
// 한 화면에 패널이 여러 개 있어도 제목 id가 겹치지 않게
const titleId = useId()
</script>

<template>
  <section class="evidence" :aria-labelledby="titleId">
    <h3 :id="titleId">근거 <span v-if="total" class="count">{{ total }}</span></h3>

    <template v-if="total">
      <div v-if="grouped.docs.length" class="group">
        <h4>참고 문서</h4>
        <ul class="docs">
          <li v-for="d in grouped.docs" :key="d.key" class="doc">
            <p class="doc-title">
              {{ d.docTitle }}
              <span v-if="d.section" class="section">{{ d.section }}</span>
            </p>
            <blockquote v-if="d.excerpt" class="excerpt">{{ d.excerpt }}</blockquote>
            <p v-if="d.docId" class="hint">{{ d.docId }}</p>
          </li>
        </ul>
      </div>

      <div v-if="grouped.tools.length" class="group">
        <h4>조회한 데이터</h4>
        <ul class="tools">
          <li v-for="t in grouped.tools" :key="t.key" class="tool-chip">{{ t.label }}</li>
        </ul>
      </div>
    </template>

    <p v-else-if="showEmpty" class="muted">이 답에 붙은 근거가 없습니다.</p>
  </section>
</template>

<style scoped>
.evidence { margin-top: 16px; display: flex; flex-direction: column; gap: 10px; }
.evidence h3 { margin: 0; display: flex; align-items: center; gap: 6px; }
.count { font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px; background: var(--brand-soft); color: var(--brand-strong); }
.group h4 { margin: 0 0 6px; font-size: 13px; color: var(--ink-2); }
.docs, .tools { list-style: none; margin: 0; padding: 0; }
.docs { display: flex; flex-direction: column; gap: 8px; }
.doc { border: 1px solid var(--line); border-left: 4px solid var(--brand); border-radius: 10px; padding: 10px 12px; }
.doc-title { margin: 0; font-weight: 600; font-size: 14px; display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.section { font-weight: 500; font-size: 12px; color: var(--ink-3); padding: 1px 6px; border-radius: 6px; background: #eef2f6; }
.excerpt { margin: 6px 0 0; padding: 0 0 0 10px; border-left: 2px solid var(--line); color: var(--ink-2); font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
.doc .hint { margin: 6px 0 0; }
.tools { display: flex; flex-wrap: wrap; gap: 6px; }
.tool-chip { font-size: 12px; padding: 3px 9px; border-radius: 999px; background: var(--info-soft); color: #1d4ed8; }
</style>
