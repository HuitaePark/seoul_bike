<!--
성과 리포트 (PRD 10장, FR-16). 운영사 책임자가 날짜와 구역을 골라 정책 4종의 지표를 비교한다.
GET /api/kpi (시뮬레이터 결과). 결과가 없으면(404·빈 목록) 없다고 보인다. 시간대별 그래프는 계약에 자료가 없어 두지 않는다.
-->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { REGIONS, todayInSeoul } from '../../shared/region/regions'

type Policy = Schemas['Policy']
type PolicyKpi = Schemas['PolicyKpi']
type Metric = keyof Omit<PolicyKpi, 'policy'>

const policyLabel: Record<Policy, string> = { NONE: '재배치 없음', GREEDY: '단순 규칙', CURRENT_OPS: '현행 운영', AGENT: '본 시스템' }
const policyColor: Record<Policy, string> = { NONE: '#cbd5e1', GREEDY: '#94a3b8', CURRENT_OPS: '#64748b', AGENT: '#0f9d58' }
const policies: Policy[] = ['NONE', 'GREEDY', 'CURRENT_OPS', 'AGENT']

// 시뮬레이터는 지난 날짜를 재생한다. 처음에는 어제(서울)를 본다.
const yesterday = new Date(Date.parse(`${todayInSeoul()}T00:00:00+09:00`) - 86_400_000)
const date = ref(todayInSeoul(yesterday))
const regionId = ref('N02')
const regionName = computed(() => REGIONS.find((r) => r.id === regionId.value)?.name ?? regionId.value)
const rows = ref<PolicyKpi[]>([])
const state = ref<'loading' | 'ready' | 'empty' | 'error'>('loading')
const error = ref('')
let ctl: AbortController | null = null

async function load() {
  ctl?.abort()
  const mine = (ctl = new AbortController())
  state.value = 'loading'
  try {
    const { data, error: problem, response } = await api.GET('/api/kpi', {
      params: { query: { date: date.value, regionId: regionId.value, policies } },
      // 계약: policies=NONE,GREEDY,... (form, explode: false)
      querySerializer: { array: { style: 'form', explode: false } },
      signal: mine.signal,
    })
    if (mine.signal.aborted) return
    if (response.status === 404 || (data && !data.length)) {
      rows.value = []
      state.value = 'empty'
    } else if (problem || !data) {
      error.value = problemMessage(problem)
      state.value = 'error'
    } else {
      rows.value = data
      state.value = 'ready'
    }
  } catch (e) {
    if (mine.signal.aborted) return
    error.value = e instanceof Error ? e.message : String(e)
    state.value = 'error'
  }
}
watch([date, regionId], load, { immediate: true })

// 결과에 있는 정책만 (계약상 policies 중 일부만 올 수 있다)
const shown = computed(() => policies.filter((p) => rows.value.some((r) => r.policy === p)))
const of = (p: Policy) => rows.value.find((r) => r.policy === p)
const pct = (a: number, b: number) => (b ? Math.round(((a - b) / b) * 100) : 0)

const metrics: { key: Metric; label: string; unit: string; better: 'low' | 'high'; main?: boolean }[] = [
  { key: 'emptyStationMinutes', label: '빈 대여소-분', unit: '분', better: 'low', main: true },
  { key: 'overflowStationMinutes', label: '과적재 대여소-분', unit: '분', better: 'low' },
  { key: 'bikesMovedPerTruckHour', label: '트럭-시간당 해소 대수', unit: '대', better: 'high' },
]

// 본 시스템과 현행 운영 결과가 둘 다 있을 때만 비교 카드를 보인다
const hero = computed(() => {
  const agent = of('AGENT')
  const ops = of('CURRENT_OPS')
  if (!agent || !ops) return []
  return metrics.map((m) => {
    const diff = pct(agent[m.key], ops[m.key])
    return { ...m, agent: agent[m.key], ops: ops[m.key], diff, good: m.better === 'low' ? diff < 0 : diff > 0 }
  })
})

function max(key: Metric) {
  return Math.max(...rows.value.map((r) => r[key]), 1)
}
const fmt = (n: number) => n.toLocaleString('ko-KR')
</script>

<template>
  <div class="page-head">
    <div>
      <h1>성과 리포트</h1>
      <p>{{ regionName }} · 같은 날짜, 같은 수요로 정책별 시뮬레이션한 결과</p>
    </div>
    <div class="filters">
      <label>날짜<input v-model="date" type="date" :max="todayInSeoul()" required /></label>
      <label>구역
        <select v-model="regionId">
          <option v-for="r in REGIONS" :key="r.id" :value="r.id">{{ r.name }} ({{ r.id }})</option>
        </select>
      </label>
    </div>
  </div>

  <section v-if="state !== 'ready'" class="card empty" role="status">
    <template v-if="state === 'loading'">성과 결과를 읽는 중</template>
    <template v-else-if="state === 'empty'">
      <strong>{{ date }} {{ regionName }}의 성과 결과가 없다.</strong>
      <span class="hint">시뮬레이터 결과가 backend(GET /api/kpi)에 들어와야 보인다.</span>
    </template>
    <template v-else>
      <strong>성과 결과를 읽지 못했다.</strong>
      <span class="hint">{{ error }}</span>
      <button class="ghost" @click="load">다시 시도</button>
    </template>
  </section>

  <template v-else>
    <section v-if="hero.length" class="hero">
      <div v-for="h in hero" :key="h.key" :class="['hero-card', { main: h.main }]">
        <span class="hero-label">{{ h.label }}<template v-if="h.main"> · 주 지표</template></span>
        <strong :class="{ good: h.good }">{{ h.diff > 0 ? '+' : '' }}{{ h.diff }}%</strong>
        <span class="hint">현행 운영 {{ fmt(h.ops) }}{{ h.unit }} → 본 시스템 {{ fmt(h.agent) }}{{ h.unit }}</span>
      </div>
    </section>

    <div class="two">
      <section class="card">
        <h2>정책별 비교</h2>
        <div v-for="m in metrics" :key="m.key" class="metric">
          <h3>{{ m.label }} <span class="hint">{{ m.better === 'low' ? '낮을수록 좋다' : '높을수록 좋다' }}</span></h3>
          <div v-for="p in shown" :key="p" class="bar-row">
            <span class="bar-label">{{ policyLabel[p] }}</span>
            <div class="bar"><span :style="{ width: `${(of(p)![m.key] / max(m.key)) * 100}%`, background: policyColor[p] }" /></div>
            <span :class="['bar-val', { agent: p === 'AGENT' }]">{{ fmt(of(p)![m.key]) }}</span>
          </div>
        </div>
      </section>

      <section class="card">
        <h2>표</h2>
        <table class="tbl">
          <thead>
            <tr><th>정책</th><th>빈 대여소-분</th><th>과적재 대여소-분</th><th>트럭-시간당</th></tr>
          </thead>
          <tbody>
            <tr v-for="p in shown" :key="p" :class="{ agent: p === 'AGENT' }">
              <td>{{ policyLabel[p] }}</td>
              <td>{{ fmt(of(p)!.emptyStationMinutes) }}</td>
              <td>{{ fmt(of(p)!.overflowStationMinutes) }}</td>
              <td>{{ of(p)!.bikesMovedPerTruckHour }}</td>
            </tr>
          </tbody>
        </table>
      </section>
    </div>
  </template>
</template>

<style scoped>
.filters { display: flex; gap: 10px; flex-wrap: wrap; }
.filters label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--ink-3); font-weight: 600; }
.empty { display: flex; flex-direction: column; align-items: flex-start; gap: 6px; padding: 28px 24px; }

.hero { display: grid; grid-template-columns: 1.3fr 1fr 1fr; gap: 14px; margin-bottom: 18px; }
.hero-card { background: #fff; border: 1px solid var(--line); border-radius: var(--radius); padding: 18px 20px; display: flex; flex-direction: column; gap: 6px; box-shadow: var(--shadow); }
.hero-card.main { background: linear-gradient(135deg, #0b7a44, #0f9d58); color: #fff; border: 0; }
.hero-card.main .hint, .hero-card.main .hero-label { color: rgb(255 255 255 / 0.85); }
.hero-label { font-size: 13px; font-weight: 600; color: var(--ink-3); }
.hero-card strong { font-size: 36px; letter-spacing: -0.03em; color: var(--empty); }
.hero-card strong.good { color: var(--brand); }
.hero-card.main strong { color: #fff; font-size: 44px; }

.two { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.1fr); gap: 18px; align-items: start; }
.metric + .metric { margin-top: 20px; }
.metric h3 { font-size: 14px; display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 8px; }
.bar-row { display: grid; grid-template-columns: 76px 1fr 64px; gap: 10px; align-items: center; margin-bottom: 6px; font-size: 13px; }
.bar-label { color: var(--ink-2); }
.bar { height: 14px; background: #f1f5f9; border-radius: 6px; overflow: hidden; }
.bar span { display: block; height: 100%; border-radius: 6px; transition: width 0.4s ease; }
.bar-val { text-align: right; font-variant-numeric: tabular-nums; color: var(--ink-2); }
.bar-val.agent { color: var(--brand-strong); font-weight: 800; }

.tbl { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.tbl th { text-align: left; color: var(--ink-3); font-weight: 600; padding: 8px 6px; border-bottom: 1px solid var(--line); }
.tbl td { padding: 9px 6px; border-bottom: 1px solid #f1f5f9; }
.tbl tr.agent td { font-weight: 800; color: var(--brand-strong); background: var(--brand-soft); }

@media (max-width: 1000px) {
  .two, .hero { grid-template-columns: minmax(0, 1fr); }
}
</style>
