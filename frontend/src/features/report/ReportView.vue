<!--
성과 리포트 (PRD 10장, FR-16). 운영사 책임자가 날짜를 골라 정책 4종의 지표를 비교한다.
지금은 더미 데이터로 화면만 잡는다. 붙일 API: GET /api/kpi
-->
<script setup lang="ts">
import { computed, ref } from 'vue'
import type { Schemas } from '../../shared/api/client'

type Policy = Schemas['Policy']
type PolicyKpi = Schemas['PolicyKpi']

const policyLabel: Record<Policy, string> = { NONE: '재배치 없음', GREEDY: '단순 규칙', CURRENT_OPS: '현행 운영', AGENT: '본 시스템' }
const policyColor: Record<Policy, string> = { NONE: '#cbd5e1', GREEDY: '#94a3b8', CURRENT_OPS: '#64748b', AGENT: '#0f9d58' }
const policies: Policy[] = ['NONE', 'GREEDY', 'CURRENT_OPS', 'AGENT']

// 날짜별 더미 결과. 실제 값은 시뮬레이터가 낸다.
const dates = ['2025-10-04', '2025-10-15', '2025-11-02']
const dateNote: Record<string, string> = {
  '2025-10-04': '여의도 불꽃축제 (토)',
  '2025-10-15': '평일 기준일 (수)',
  '2025-11-02': '비 온 뒤 갬 (일)',
}
const data: Record<string, PolicyKpi[]> = {
  '2025-10-04': [
    { policy: 'NONE', emptyStationMinutes: 18420, overflowStationMinutes: 9310, bikesMovedPerTruckHour: 0 },
    { policy: 'GREEDY', emptyStationMinutes: 12950, overflowStationMinutes: 6120, bikesMovedPerTruckHour: 11.2 },
    { policy: 'CURRENT_OPS', emptyStationMinutes: 11480, overflowStationMinutes: 5480, bikesMovedPerTruckHour: 12.6 },
    { policy: 'AGENT', emptyStationMinutes: 6890, overflowStationMinutes: 3270, bikesMovedPerTruckHour: 17.9 },
  ],
  '2025-10-15': [
    { policy: 'NONE', emptyStationMinutes: 12300, overflowStationMinutes: 6040, bikesMovedPerTruckHour: 0 },
    { policy: 'GREEDY', emptyStationMinutes: 8610, overflowStationMinutes: 4020, bikesMovedPerTruckHour: 12.1 },
    { policy: 'CURRENT_OPS', emptyStationMinutes: 7720, overflowStationMinutes: 3650, bikesMovedPerTruckHour: 13.4 },
    { policy: 'AGENT', emptyStationMinutes: 5020, overflowStationMinutes: 2410, bikesMovedPerTruckHour: 16.8 },
  ],
  '2025-11-02': [
    { policy: 'NONE', emptyStationMinutes: 9800, overflowStationMinutes: 7200, bikesMovedPerTruckHour: 0 },
    { policy: 'GREEDY', emptyStationMinutes: 7350, overflowStationMinutes: 5110, bikesMovedPerTruckHour: 10.4 },
    { policy: 'CURRENT_OPS', emptyStationMinutes: 6610, overflowStationMinutes: 4700, bikesMovedPerTruckHour: 11.8 },
    { policy: 'AGENT', emptyStationMinutes: 4420, overflowStationMinutes: 2950, bikesMovedPerTruckHour: 15.1 },
  ],
}
// 시간대별 빈 대여소 수 (06~23시). 정책마다 기준선에 비율을 곱한 모양이다.
const hourly = [6, 9, 21, 34, 28, 17, 14, 13, 15, 18, 22, 27, 36, 41, 33, 24, 16, 10]
const factor: Record<Policy, number> = { NONE: 1, GREEDY: 0.72, CURRENT_OPS: 0.64, AGENT: 0.4 }

const date = ref(dates[0]!)
const rows = computed(() => data[date.value]!)
const of = (p: Policy) => rows.value.find((r) => r.policy === p)!
const pct = (a: number, b: number) => Math.round(((a - b) / b) * 100)

const metrics: { key: keyof Omit<PolicyKpi, 'policy'>; label: string; unit: string; better: 'low' | 'high'; main?: boolean }[] = [
  { key: 'emptyStationMinutes', label: '빈 대여소-분', unit: '분', better: 'low', main: true },
  { key: 'overflowStationMinutes', label: '과적재 대여소-분', unit: '분', better: 'low' },
  { key: 'bikesMovedPerTruckHour', label: '트럭-시간당 해소 대수', unit: '대', better: 'high' },
]

const hero = computed(() =>
  metrics.map((m) => {
    const agent = of('AGENT')[m.key]
    const ops = of('CURRENT_OPS')[m.key]
    const diff = pct(agent, ops)
    return { ...m, agent, ops, diff, good: m.better === 'low' ? diff < 0 : diff > 0 }
  }),
)

function max(key: keyof Omit<PolicyKpi, 'policy'>) {
  return Math.max(...rows.value.map((r) => r[key]))
}

const W = 720
const H = 220
const P = 28
const lines = computed(() => {
  const top = Math.max(...hourly)
  const x = (i: number) => P + (i / (hourly.length - 1)) * (W - P * 2)
  const y = (v: number) => H - P - (v / top) * (H - P * 2)
  return policies.map((p) => ({
    policy: p,
    d: hourly.map((v, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v * factor[p]).toFixed(1)}`).join(' '),
  }))
})
const ticks = [0, 3, 6, 9, 12, 15, 17].map((i) => ({ x: P + (i / (hourly.length - 1)) * (W - P * 2), label: `${6 + i}시` }))
const fmt = (n: number) => n.toLocaleString('ko-KR')
</script>

<template>
  <div class="page-head">
    <div>
      <h1>성과 리포트 <span class="dummy-tag">더미 데이터</span></h1>
      <p>광진·성동 · 트럭 3대 · 같은 날짜, 같은 수요로 정책 4종을 시뮬레이션한 결과</p>
    </div>
    <div class="dates">
      <button v-for="d in dates" :key="d" :class="['date', { on: d === date }]" @click="date = d">
        <strong>{{ d }}</strong>
        <small>{{ dateNote[d] }}</small>
      </button>
    </div>
  </div>

  <section class="hero">
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
        <div v-for="p in policies" :key="p" class="bar-row">
          <span class="bar-label">{{ policyLabel[p] }}</span>
          <div class="bar"><span :style="{ width: `${(of(p)[m.key] / max(m.key)) * 100}%`, background: policyColor[p] }" /></div>
          <span :class="['bar-val', { agent: p === 'AGENT' }]">{{ fmt(of(p)[m.key]) }}</span>
        </div>
      </div>
    </section>

    <section class="card">
      <h2>시간대별 빈 대여소 수</h2>
      <svg :viewBox="`0 0 ${W} ${H}`" class="chart" role="img" aria-label="시간대별 빈 대여소 수">
        <line v-for="t in ticks" :key="t.label" :x1="t.x" :x2="t.x" :y1="P" :y2="H - P" stroke="#eef2f6" />
        <text v-for="t in ticks" :key="`l${t.label}`" :x="t.x" :y="H - 8" class="tick">{{ t.label }}</text>
        <path
          v-for="l in lines"
          :key="l.policy"
          :d="l.d"
          fill="none"
          :stroke="policyColor[l.policy]"
          :stroke-width="l.policy === 'AGENT' ? 3.5 : 2"
          stroke-linejoin="round"
        />
      </svg>
      <div class="legend">
        <span v-for="p in policies" :key="p"><i :style="{ background: policyColor[p] }" />{{ policyLabel[p] }}</span>
      </div>

      <h2 class="mt">표</h2>
      <table class="tbl">
        <thead>
          <tr><th>정책</th><th>빈 대여소-분</th><th>과적재 대여소-분</th><th>트럭-시간당</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in policies" :key="p" :class="{ agent: p === 'AGENT' }">
            <td>{{ policyLabel[p] }}</td>
            <td>{{ fmt(of(p).emptyStationMinutes) }}</td>
            <td>{{ fmt(of(p).overflowStationMinutes) }}</td>
            <td>{{ of(p).bikesMovedPerTruckHour }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </div>
</template>

<style scoped>
.dates { display: flex; gap: 8px; }
.date { display: flex; flex-direction: column; align-items: flex-start; gap: 2px; background: #fff; color: var(--ink); border: 1px solid var(--line); padding: 8px 14px; }
.date:hover:not(:disabled) { background: #f8fafc; }
.date small { font-size: 11px; color: var(--ink-3); font-weight: 500; }
.date.on { border-color: var(--brand); box-shadow: inset 0 0 0 1px var(--brand); background: var(--brand-soft); }
.date.on:hover:not(:disabled) { background: var(--brand-soft); }

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

.chart { width: 100%; height: auto; display: block; }
.chart path { transition: d 0.4s; }
.tick { font-size: 11px; fill: var(--ink-3); text-anchor: middle; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; font-size: 12px; color: var(--ink-2); margin-top: 6px; }
.legend span { display: inline-flex; align-items: center; gap: 6px; }
.legend i { width: 14px; height: 4px; border-radius: 2px; display: inline-block; }
.mt { margin-top: 20px; }
.tbl { width: 100%; border-collapse: collapse; font-size: 13px; font-variant-numeric: tabular-nums; }
.tbl th { text-align: left; color: var(--ink-3); font-weight: 600; padding: 8px 6px; border-bottom: 1px solid var(--line); }
.tbl td { padding: 9px 6px; border-bottom: 1px solid #f1f5f9; }
.tbl tr.agent td { font-weight: 800; color: var(--brand-strong); background: var(--brand-soft); }

@media (max-width: 1000px) {
  .two, .hero { grid-template-columns: minmax(0, 1fr); }
}
</style>
