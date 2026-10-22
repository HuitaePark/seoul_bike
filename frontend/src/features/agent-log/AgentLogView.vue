<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import {
  ALL_GROUPS, clock, commandHelp, emptyFilter, groupLabel, isProblem, kindLabel, parseCommand, rationales, shortId,
  sourceLabel, summarize, visible, type KindGroup, type TaskSummary,
} from './agentLog'
import { useAgentLog } from './useAgentLog'

// 에이전트 추론 로그 (관리자). 작업 콘솔의 진행 표시와 따로, 모델이 어떤 도구를 왜 골랐는지와 그 결과·안전장치 판단을
// 터미널처럼 한 줄씩 본다. 권한은 backend가 /api/admin/** 에서 MANAGER만 허용한다. 값은 agent가 마스킹한 것이다.

const log = useAgentLog()
const filter = ref(emptyFilter())
const verbose = ref(false)
const opened = ref<Set<number>>(new Set())
const command = ref('')
const notes = ref<string[]>([])
/** clear: 이 id까지의 줄은 화면에서만 감춘다 (기록은 남는다) */
const hiddenUpTo = ref(0)

const statusLabel: Record<TaskSummary['status'], string> = {
  RUNNING: '실행 중', WAITING_APPROVAL: '승인 대기', DONE: '완료', FAILED: '실패',
}
const stateLabel = computed(() => {
  if (log.paused.value) return `멈춤 · 새 기록 ${log.held.value.length}건`
  return { connecting: '연결 중', live: '실시간', reconnecting: '다시 연결 중', failed: '끊김' }[log.state.value]
})

const tasks = computed(() => summarize(log.traces.value))
const lines = computed(() => visible(log.traces.value.filter((t) => t.id > hiddenUpTo.value), filter.value))
const focused = computed(() => tasks.value.find((t) => t.taskId === filter.value.taskId) ?? null)

const screen = ref<HTMLElement | null>(null)
const atBottom = ref(true)
function onScroll() {
  const el = screen.value
  if (el) atBottom.value = el.scrollHeight - el.scrollTop - el.clientHeight < 48
}
async function toBottom() {
  await nextTick()
  screen.value?.scrollTo({ top: screen.value.scrollHeight })
  atBottom.value = true
}
// 따라가는 중이고 맨 아래를 보고 있을 때만 새 줄로 내려간다. 위로 올려 읽는 동안에는 움직이지 않는다
watch(() => lines.value.length, () => {
  if (atBottom.value) void toBottom()
})

function selectTask(taskId: string | null) {
  filter.value = { ...filter.value, taskId }
  if (taskId) void log.loadTask(taskId)
  void toBottom()
}

function toggleGroup(group: KindGroup) {
  const groups = filter.value.groups.includes(group)
    ? filter.value.groups.filter((g) => g !== group)
    : [...filter.value.groups, group]
  filter.value = { ...filter.value, groups }
}

function toggleLine(id: number) {
  const next = new Set(opened.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  opened.value = next
}

function togglePause() {
  if (log.paused.value) {
    log.follow()
    void toBottom()
  } else {
    log.pause()
  }
}

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2)
}

/** 화면 아래 명령 줄 (help로 목록). 버튼·칩과 같은 일을 한다 */
async function run() {
  const input = command.value.trim()
  command.value = ''
  if (!input) return
  const cmd = parseCommand(input, tasks.value.map((t) => t.taskId))
  notes.value = [`agent-log$ ${input}`]
  switch (cmd.type) {
    case 'task':
      selectTask(cmd.taskId)
      notes.value.push(cmd.taskId ? `작업 ${shortId(cmd.taskId)}만 본다` : '모든 작업을 본다')
      break
    case 'kind':
      filter.value = { ...filter.value, groups: cmd.groups }
      notes.value.push(`보이는 종류: ${cmd.groups.map((g) => groupLabel[g]).join(', ')}`)
      break
    case 'grep':
      filter.value = { ...filter.value, text: cmd.text }
      notes.value.push(cmd.text ? `'${cmd.text}'가 있는 줄만 본다` : '찾기를 풀었다')
      break
    case 'pause':
      log.pause()
      notes.value.push('멈췄다. 새 기록은 모아 둔다 (follow로 이어 본다)')
      break
    case 'follow':
      log.follow()
      notes.value.push('실시간으로 따라간다')
      break
    case 'clear':
      hiddenUpTo.value = log.traces.value.at(-1)?.id ?? 0
      log.clearHeld()
      notes.value.push('화면을 비웠다. 기록은 남아 있다 (새로 고치면 다시 보인다)')
      break
    case 'more':
      await log.loadOlder(filter.value.taskId)
      notes.value.push('지난 기록을 더 읽었다')
      break
    case 'verbose':
      verbose.value = cmd.on
      notes.value.push(cmd.on ? '인자·결과를 펼친다' : '인자·결과를 접는다')
      break
    case 'help':
      notes.value.push(...commandHelp)
      break
    case 'error':
      notes.value.push(cmd.message)
      break
  }
  void toBottom()
}

onMounted(async () => {
  await log.start()
  void toBottom()
})
</script>

<template>
  <section class="agent-log">
    <div class="page-head">
      <div>
        <h1>에이전트 추론 로그</h1>
        <p>모델이 어떤 도구를 왜 골랐는지, 실행 인자·결과와 안전장치 판단을 한 줄씩 본다. 작업 콘솔과 따로 기록되며 관리자만 본다.</p>
      </div>
      <div class="head-actions">
        <span class="conn" :class="log.paused.value ? 'paused' : log.state.value" role="status">{{ stateLabel }}</span>
        <button class="ghost" type="button" @click="togglePause">{{ log.paused.value ? '이어 보기' : '멈춤' }}</button>
      </div>
    </div>
    <p v-if="log.error.value" class="error">{{ log.error.value }}</p>

    <div class="layout">
      <aside class="card task-pane" aria-label="로그에 나온 작업">
        <h2>작업 <span class="muted">{{ tasks.length }}</span></h2>
        <ul class="tasks">
          <li :class="{ active: !filter.taskId }" @click="selectTask(null)">
            <strong>전체 작업</strong>
            <small class="muted">새 기록이 들어오는 대로</small>
          </li>
          <li
            v-for="t in tasks"
            :key="t.taskId"
            :class="{ active: filter.taskId === t.taskId }"
            :title="t.taskId"
            @click="selectTask(t.taskId)"
          >
            <span class="row">
              <span class="badge" :class="t.status">{{ statusLabel[t.status] }}</span>
              <code>{{ shortId(t.taskId) }}</code>
              <span class="type">{{ t.taskType ?? '' }}</span>
            </span>
            <small class="muted">{{ clock(t.lastAt) }} · 판단 {{ t.decisions }}번 · {{ t.lines }}줄</small>
          </li>
        </ul>
      </aside>

      <div class="card term-pane">
        <div class="toolbar">
          <div class="chips" role="group" aria-label="줄 종류">
            <button
              v-for="g in ALL_GROUPS"
              :key="g"
              type="button"
              class="chip"
              :aria-pressed="filter.groups.includes(g)"
              @click="toggleGroup(g)"
            >{{ groupLabel[g] }}</button>
          </div>
          <input v-model="filter.text" class="find" type="search" placeholder="제목·이유에서 찾기" aria-label="로그에서 찾기" />
          <label class="check"><input v-model="verbose" type="checkbox" /> 인자·결과 펼치기</label>
        </div>
        <p v-if="focused" class="focus">
          작업 <code>{{ focused.taskId }}</code> · {{ focused.taskType ?? '' }} · 근무조 {{ focused.shiftId }}
          <button type="button" class="ghost small" @click="selectTask(null)">전체 보기</button>
        </p>

        <div ref="screen" class="term" role="log" aria-label="에이전트 추론 로그" @scroll="onScroll">
          <button v-if="log.hasOlder.value || filter.taskId" type="button" class="older" @click="log.loadOlder(filter.taskId)">
            지난 기록 더 보기
          </button>
          <p v-if="!lines.length" class="empty">
            {{ log.traces.value.length ? '조건에 맞는 줄이 없다' : '아직 기록이 없다. 작업 콘솔에서 작업을 만들면 여기에 모델의 판단이 쌓인다' }}
          </p>
          <div v-for="t in lines" :key="t.id" class="line" :class="[t.kind, { problem: isProblem(t) }]">
              <span class="time">{{ clock(t.occurredAt) }}</span>
              <button type="button" class="task-id" :title="`작업 ${t.taskId}만 보기`" @click="selectTask(t.taskId)">{{ shortId(t.taskId) }}</button>
              <span class="src" :class="t.source">{{ sourceLabel[t.source] }}</span>
              <span class="turn">{{ t.turn ? `턴${t.turn}` : '' }}</span>
              <button
                type="button"
                class="kind"
                :aria-expanded="verbose || opened.has(t.id)"
                :title="'세부 내용 ' + (verbose || opened.has(t.id) ? '접기' : '펼치기')"
                @click="toggleLine(t.id)"
              >{{ kindLabel[t.kind] }}</button>
              <span class="title">{{ t.title }}</span>
            <div v-for="(r, i) in rationales(t)" :key="i" class="why">└ <b>{{ r.name }}</b> {{ r.rationale }}</div>
            <template v-if="t.kind === 'GUARD'">
              <div v-for="(r, i) in t.detail.reasons ?? []" :key="i" class="why">- {{ r }}</div>
            </template>
            <div v-if="t.kind === 'DECISION' && t.detail.thought" class="why">생각: {{ t.detail.thought }}</div>
            <pre v-if="verbose || opened.has(t.id)" class="detail">{{ pretty(t.detail) }}</pre>
          </div>
          <div v-for="(n, i) in notes" :key="`n${i}`" class="note">{{ n }}</div>
        </div>
        <button v-if="!atBottom" type="button" class="jump" @click="toBottom">맨 아래로</button>

        <form class="prompt" @submit.prevent="run">
          <label for="agent-log-cmd">agent-log$</label>
          <input
            id="agent-log-cmd"
            v-model="command"
            autocomplete="off"
            spellcheck="false"
            placeholder="help · task <ID> · kind decision · grep <글자> · pause · follow"
          />
        </form>
      </div>
    </div>
  </section>
</template>

<style scoped>
.head-actions { display: flex; align-items: center; gap: 10px; }
.conn { font-size: 13px; font-weight: 700; padding: 4px 10px; border-radius: 999px; background: #eef2f6; color: var(--ink-2); }
.conn.live { background: #dcfce7; color: #166534; }
.conn.reconnecting, .conn.connecting { background: var(--overflow-soft); color: #92400e; }
.conn.failed { background: var(--empty-soft); color: #991b1b; }
.conn.paused { background: var(--info-soft); color: #1d4ed8; }

.layout { display: grid; grid-template-columns: minmax(240px, 300px) minmax(0, 1fr); gap: 16px; align-items: start; }
@media (max-width: 900px) { .layout { grid-template-columns: minmax(0, 1fr); } }

.task-pane { padding: 14px; max-height: calc(100vh - 210px); overflow-y: auto; }
.task-pane h2 { font-size: 15px; }
.task-pane li { display: flex; flex-direction: column; gap: 4px; }
.task-pane .row { display: flex; align-items: center; gap: 4px; }
.task-pane code { font-size: 12px; }
.task-pane .type { font-size: 12px; color: var(--ink-3); }

.term-pane { padding: 14px; display: flex; flex-direction: column; gap: 10px; position: relative; }
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 10px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; }
.chip { padding: 5px 11px; font-size: 12px; border-radius: 999px; background: #eef2f6; color: var(--ink-3); border: 1px solid var(--line); }
.chip:hover:not(:disabled) { background: #e2e8f0; }
.chip[aria-pressed='true'] { background: var(--brand-soft); color: var(--brand-strong); border-color: var(--brand); }
.find { flex: 1 1 180px; min-width: 140px; padding: 6px 10px; font-size: 13px; }
.check { flex-direction: row; align-items: center; gap: 6px; font-size: 13px; }
.focus { margin: 0; font-size: 13px; color: var(--ink-2); display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.ghost.small { padding: 3px 10px; font-size: 12px; }

/* 터미널: 어두운 바탕에 줄 종류별 글자색. 그림자는 쓰지 않는다 (색 그림자 금지 규칙) */
.term {
  --t-bg: #0f172a; --t-fg: #e2e8f0; --t-dim: #94a3b8;
  background: var(--t-bg); color: var(--t-fg); border-radius: 10px; padding: 12px 14px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, 'D2Coding', monospace; font-size: 12.5px; line-height: 1.55;
  height: calc(100vh - 320px); min-height: 360px; overflow-y: auto; overflow-x: hidden;
}
.term .empty, .term .note { color: var(--t-dim); margin: 4px 0; white-space: pre-wrap; }
.term .note:first-of-type { margin-top: 10px; }
.older { display: block; margin: 0 auto 8px; padding: 4px 12px; font-size: 12px; background: #1e293b; color: var(--t-fg); }
.older:hover:not(:disabled) { background: #334155; }
/* 한 줄 = 시각 · 작업 · 출처 · 턴 · 종류 · 제목. 이유·세부는 제목 칸 아래로 */
.line {
  display: grid; grid-template-columns: 5.4em 5.8em 3.2em 3.2em 6.6em minmax(0, 1fr); column-gap: 8px; align-items: baseline;
  padding: 1px 2px; border-radius: 4px;
}
.line.problem { background: rgb(239 68 68 / 0.14); }
.time, .turn { color: var(--t-dim); }
.task-id, .kind {
  justify-self: start; padding: 0 4px; margin: 0; font: inherit; font-weight: 600; border-radius: 4px; background: transparent;
  color: var(--t-dim);
}
.task-id:hover:not(:disabled), .kind:hover:not(:disabled) { background: #1e293b; }
.src { color: var(--t-dim); }
.src.CODE { color: #fdba74; }
.src.GRAPH { color: #67e8f9; }
.kind { text-align: left; }
.title { min-width: 0; overflow-wrap: anywhere; white-space: pre-wrap; }
.why { grid-column: 6 / -1; color: #cbd5e1; overflow-wrap: anywhere; white-space: pre-wrap; }
.why b { color: #fde68a; font-weight: 600; }
.detail { grid-column: 6 / -1; margin: 4px 0 6px; padding: 8px 10px; background: #1e293b; border-radius: 6px; color: #cbd5e1; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
@media (max-width: 900px) {
  .line { grid-template-columns: 5.4em 5.8em minmax(0, 1fr); }
  .src, .turn { display: none; }
  .kind { grid-column: 3; }
  .title, .why, .detail { grid-column: 1 / -1; }
}

.DECISION .kind, .DECISION .title { color: #fde047; }
.TOOL_CALL .kind { color: #93c5fd; }
.TOOL_RESULT .kind { color: #86efac; }
.GUARD .kind, .GUARD .title { color: #fca5a5; }
.RECOVERY .kind { color: #f0abfc; }
.APPROVAL_WAIT .kind, .APPROVAL_WAIT .title { color: #fdba74; }
.ROUTE .kind { color: #67e8f9; }
.RUN_START .kind, .RUN_END .kind { color: #f8fafc; }
.RUN_START .title, .RUN_END .title { font-weight: 700; }
.problem .kind, .problem .title { color: #fca5a5; }

.jump { position: absolute; right: 30px; bottom: 74px; padding: 5px 12px; font-size: 12px; }
.prompt { flex-direction: row; align-items: center; gap: 8px; margin: 0; padding: 8px 12px; border-radius: 10px; background: #0f172a; }
.prompt label { flex: none; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px; color: #86efac; font-weight: 600; }
.prompt input {
  flex: 1; min-width: 0; padding: 4px 0; border: 0; background: transparent; color: #e2e8f0;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 13px;
}
.prompt input:focus { outline: none; }
.prompt input::placeholder { color: #64748b; }
</style>
