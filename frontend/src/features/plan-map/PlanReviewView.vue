<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import RegionPicker from '../../shared/region/RegionPicker.vue'
import StationDetail from './StationDetail.vue'
import PlanMap from './PlanMap.vue'
import { usePlanReview } from './usePlanReview'
import { editableStop, isLoadInvalid, label, loadPercent, numberText, planLabels, reasonText, riskLabels, routeSummary, stationName, stopLabels, taskLabels, timeText } from './model.ts'

const props = withDefaults(defineProps<{ demo?: boolean }>(), { demo: false })
const review = usePlanReview(() => props.demo)
const { manager, query, loadedQuery, plan, stations, stationMap, task, taskInput, loading, sending, loaded, errors, notice,
  selectedTruck, selectedStation, selectedRoute, sortedStops, eligible, excluded, reviewed, comment, blockReason, changedQuery, streamState } = review
const searchText = ref('')
const risk = ref('ALL')
const stationRole = ref('ALL')
const filteredStations = computed(() => stations.value.filter(s =>
  `${s.stationId} ${s.name}`.toLocaleLowerCase().includes(searchText.value.toLocaleLowerCase()) &&
  (risk.value === 'ALL' || s.risk === risk.value) && (stationRole.value === 'ALL' || s.role === stationRole.value)))
const unserved = computed(() => [...(plan.value?.unserved ?? [])].sort((a, b) => b.priority - a.priority))
const totalStops = computed(() => plan.value?.routes.reduce((n, r) => n + r.stops.length, 0) ?? 0)
const streamLabels: Record<string, string> = { idle: '연결 전', connecting: '연결 중', open: '실시간 연결', reconnecting: '재연결 중', done: '작업 종료', failed: '연결 실패' }
// 권역을 바꾸면 근무조 ID 앞의 권역도 같이 바꾼다 (권역:날짜:근무조). 권역을 비웠다가(`:날짜:AM`) 다시 넣어도 붙인다.
watch(() => query.regionId, (next, prev) => {
  if (query.shiftId.startsWith(`${prev}:`)) query.shiftId = `${next}${query.shiftId.slice(prev.length)}`
})
function selectTruck(id: string) {
  selectedTruck.value = id
  selectedStation.value = plan.value?.routes.find(r => r.truckId === id)?.stops[0]?.stationId ?? selectedStation.value
}
</script>

<template>
  <div class="plan-review">
    <div class="page-heading">
      <div><p class="eyebrow">재배치 운영</p><h1>계획 검토</h1><p class="muted">트럭 경로와 적재량을 확인하고, 미처리 거점까지 검토하세요.</p></div>
      <RouterLink :to="demo ? '/plans/review' : '/plans/review/demo'" class="text-link">{{ demo ? '실제 계획 조회' : '시연 화면 보기' }} →</RouterLink>
    </div>
    <div v-if="demo" class="demo-banner" role="note"><strong>시연 데이터 · 실제 계획에 반영되지 않습니다</strong><span>위치·관측·예측·방문 순서는 검토 흐름을 확인하기 위한 가상 자료입니다.</span><button class="ghost" :disabled="sending" @click="review.reset">시연 초기화</button></div>

    <form class="panel query-form" aria-label="계획 조회" @submit.prevent="review.search">
      <RegionPicker v-model="query.regionId" label="권역 ID" :readonly="demo" :disabled="sending" required />
      <label class="shift-field">근무조 ID<input v-model="query.shiftId" :readonly="demo" :disabled="sending" required placeholder="권역:날짜:근무조"></label>
      <label>예측 구간<select v-model.number="query.horizonH" :disabled="demo || sending"><option v-for="h in 5" :key="h" :value="h">{{ h }}시간 후</option></select></label>
      <button type="submit" :disabled="sending">계획 조회</button>
      <p v-if="errors.query" class="form-message error" role="alert">{{ errors.query }}</p>
    </form>
    <p v-if="changedQuery" class="callout">조건이 바뀌었습니다. 현재 화면은 이전 조회 결과입니다. 계획을 다시 조회하세요.</p>
    <p v-if="loading" role="status" class="callout">계획과 대여소의 최신 상태를 확인하고 있습니다.</p>
    <section v-if="errors.plan" class="panel error-panel" role="alert"><h2>계획을 불러오지 못했습니다</h2><p>{{ errors.plan }}</p><button :disabled="loading || sending" @click="review.reload">다시 시도</button></section>
    <section v-else-if="!plan && !loading" class="panel empty"><h2>{{ loaded ? '이 근무조의 계획이 아직 없습니다' : '검토할 근무조를 조회하세요' }}</h2><p>작업 콘솔에서 계획 초안을 요청한 뒤 근무조 ID로 조회할 수 있습니다.</p><RouterLink to="/console">작업 콘솔로 이동 →</RouterLink></section>
    <p v-if="errors.stations" class="callout warning" role="alert">대여소 현황 조회 실패: {{ errors.stations }} · 계획에 포함된 값만 표시합니다. <button class="ghost" :disabled="loading || sending" @click="review.reload">현황 재조회</button></p>

    <template v-if="plan">
      <section class="panel plan-overview" aria-label="계획 요약">
        <div class="section-heading"><div><p class="eyebrow">{{ loadedQuery?.shiftId }}</p><h2>재배치 계획 <span class="version">v{{ plan.version }}</span><span class="pill" :class="plan.status">{{ label(planLabels, plan.status) }}</span></h2></div><button class="ghost" :disabled="loading || sending" @click="review.reload">최신 상태 확인</button></div>
        <div class="metrics"><div><strong>{{ plan.routes.length }}</strong><span>배정 트럭</span></div><div><strong>{{ totalStops }}</strong><span>예정 작업</span></div><div><strong>{{ plan.unserved.length }}</strong><span>미처리 거점</span></div><div><strong>{{ numberText(loadedQuery?.horizonH) }}시간</strong><span>예측 구간</span></div></div>
        <p v-if="plan.provisional" class="callout warning">임시 계획입니다. 확정 전 최신 예측과 경로를 다시 확인하세요.</p>
        <p class="explanation">{{ plan.explanation || '계획 설명 미제공' }}</p>
        <p class="muted plan-id">계획 ID: {{ plan.planId }}</p>
      </section>

    </template>
    <PlanMap v-if="loaded && (stations.length || plan)"
      v-model:search-text="searchText" v-model:risk="risk" v-model:station-role="stationRole"
      :stations="stations" :visible-stations="filteredStations" :plan="plan"
      :selected-truck="selectedTruck" :selected-station="selectedStation" :excluded="excluded"
      @select-truck="selectTruck" @select-station="selectedStation = $event" />
    <template v-if="plan">
      <div class="review-grid">
        <section class="panel route-panel" aria-labelledby="route-heading">
          <div class="section-heading"><h2 id="route-heading">트럭별 경로</h2><span class="muted">ETA · 한국 시간</span></div>
          <div class="truck-list" role="group" aria-label="트럭 선택">
            <button v-for="r in plan.routes" :key="r.truckId" class="truck" :class="{ selected: selectedTruck === r.truckId }" :aria-pressed="selectedTruck === r.truckId" @click="selectTruck(r.truckId)"><strong>{{ r.truckId }}</strong><span>{{ r.stops.length }}곳 · 회수 {{ routeSummary(r).pickup }} / 배치 {{ routeSummary(r).dropoff }}대</span></button>
          </div>
          <template v-if="selectedRoute">
            <div class="route-meta"><span>용량 <strong>{{ numberText(selectedRoute.capacity) }}대</strong></span><span>시작 적재 <strong>{{ numberText(selectedRoute.startLoad) }}대</strong></span><span>완료 {{ routeSummary(selectedRoute).done }} · 대기 {{ routeSummary(selectedRoute).pending }}</span></div>
            <div v-if="sortedStops.length" class="table-scroll" tabindex="0" aria-label="트럭 방문 순서표">
              <table><thead><tr><th>순서 / 대여소</th><th>작업 / ETA</th><th>작업 후 적재</th><th v-if="manager">제외</th></tr></thead><tbody>
                <tr v-for="stop in sortedStops" :key="`${selectedTruck}-${stop.seq}`" :class="{ focused: selectedStation === stop.stationId }">
                  <td><button class="station-button" :aria-label="`${stationName(stop.stationId, stationMap, stop.stationName)} 상세`" @click="selectedStation = stop.stationId"><span class="sequence">{{ stop.seq }}</span>{{ stationName(stop.stationId, stationMap, stop.stationName) }}</button><span class="small-line">{{ stop.stationId }} · {{ label(stopLabels, stop.status ?? 'PENDING') }}</span></td>
                  <td><strong class="action" :class="stop.action">{{ stop.action === 'PICKUP' ? '회수' : '배치' }} {{ stop.bikes }}대</strong><span class="small-line">{{ timeText(stop.eta) }}</span></td>
                  <td><span :class="{ 'error-text': isLoadInvalid(stop.loadAfter, selectedRoute.capacity) }">{{ numberText(stop.loadAfter) }} / {{ numberText(selectedRoute.capacity) }}대</span><div v-if="loadPercent(stop.loadAfter, selectedRoute.capacity) !== null" class="load-track" aria-hidden="true"><span :style="{ width: `${loadPercent(stop.loadAfter, selectedRoute.capacity)}%` }"></span></div><span v-if="isLoadInvalid(stop.loadAfter, selectedRoute.capacity)" class="small-line error-text">적재 범위 확인 필요</span></td>
                  <td v-if="manager"><input type="checkbox" :aria-label="`${stationName(stop.stationId, stationMap, stop.stationName)} 제외`" :checked="excluded.includes(stop.stationId)" :disabled="!!blockReason || !editableStop(stop)" @change="review.toggleExclude(stop.stationId, ($event.target as HTMLInputElement).checked)"></td>
                </tr>
              </tbody></table>
            </div>
            <p v-else class="empty-inline">이 트럭에 배정된 작업이 없습니다.</p>
          </template>
          <p v-else class="empty-inline">배정된 트럭이 없습니다.</p>
        </section>
        <StationDetail v-if="selectedStation" :id="selectedStation" :stations="stationMap" :plan="plan" :horizon="loadedQuery?.horizonH ?? 3" />
      </div>

      <section class="panel" aria-labelledby="unserved-heading"><div class="section-heading"><h2 id="unserved-heading">미처리 거점 <span class="count">{{ unserved.length }}</span></h2><span class="muted">우선순위 높은 순</span></div><p class="muted">계산 결과에서 처리하지 못한 거점입니다. 목록의 사유를 확인하세요.</p>
        <div v-if="unserved.length" class="unserved-list"><button v-for="s in unserved" :key="s.stationId" class="unserved-item" :class="{ focused: selectedStation === s.stationId }" @click="selectedStation = s.stationId"><span><strong>{{ stationName(s.stationId, stationMap, s.stationName) }}</strong><span class="small-line">{{ s.stationId }} · 우선순위 {{ numberText(s.priority) }}</span></span><span class="unserved-reason">{{ reasonText(s.reason) }}</span><span aria-hidden="true">↗</span></button></div>
        <p v-else class="empty-inline">미처리 거점이 없습니다.</p>
      </section>

      <section class="panel decision-panel" aria-labelledby="decision-heading">
        <div class="section-heading"><div><p class="eyebrow">계획 결정</p><h2 id="decision-heading">{{ manager ? '검토 후 결정' : '조회 전용' }}</h2></div><span v-if="task" class="pill">{{ label(taskLabels, task.status) }}</span></div>
        <p v-if="!manager" class="callout">책임자 계정은 계획을 조회할 수 있습니다. 승인·거절은 관리자에게 요청하세요.</p>
        <template v-else>
          <form v-if="!demo" class="task-form" @submit.prevent="review.linkTask"><label>계획 승인 작업 ID<input v-model="taskInput" :disabled="loading || sending" placeholder="작업 콘솔의 UUID" required></label><button type="submit" class="ghost" :disabled="!plan || loading || sending || changedQuery">승인 작업 연결</button><RouterLink to="/console">작업 콘솔</RouterLink></form>
          <p v-if="task" class="muted task-id">{{ task.taskId }} · {{ demo ? '시연 작업' : label(streamLabels, streamState) }}</p>
          <p v-if="task?.error" class="callout warning">작업 실패: {{ task.error }}</p>
          <p v-if="errors.task" class="error-text" role="alert">{{ errors.task }}</p>
          <p v-if="task?.pendingApproval?.summary" class="approval-summary">{{ task.pendingApproval.summary }}</p>
          <ul v-if="task?.pendingApproval?.reasons?.length" class="approval-reasons"><li v-for="(reason, index) in task.pendingApproval.reasons" :key="index">{{ reason }}</li></ul>
          <p v-if="blockReason" class="callout" data-testid="decision-block">{{ blockReason }}</p>
          <div v-if="excluded.length" class="exclusions"><strong>제외 요청 {{ excluded.length }}곳</strong><div class="chips"><button v-for="id in excluded" :key="id" class="ghost" :disabled="!!blockReason" :aria-label="`${stationName(id, stationMap)} 제외 취소`" @click="review.toggleExclude(id, false)">{{ stationName(id, stationMap) }} ×</button></div><p>새 후보를 요청합니다. 제외 후 바로 운영 계획으로 확정되지 않습니다.</p></div>
          <p v-else class="muted">제외할 대여소가 있으면 경로표에서 선택하세요. 대기 중인 {{ eligible.size }}개 거점만 선택할 수 있습니다.</p>
          <label>검토 의견 (선택, 500자 이내)<textarea v-model="comment" maxlength="500" rows="2" :disabled="!!blockReason" placeholder="승인·거절 또는 제외 요청에 남길 의견"></textarea></label>
          <label class="check-label"><input v-model="reviewed" type="checkbox" :disabled="!!blockReason || !!excluded.length">현재 v{{ plan.version }}의 트럭 경로·적재량과 미처리 사유를 확인했습니다.</label>
          <div class="decision-buttons"><button :disabled="!!blockReason || !reviewed || !!excluded.length" @click="review.decide('approve')">현재 안 최종 승인</button><button class="exclude-button" :disabled="!!blockReason || !excluded.length" @click="review.decide('exclude')">제외 후 새 안 요청</button><button class="ghost reject-button" :disabled="!!blockReason" @click="review.decide('reject')">현재 안 거절</button></div>
          <p class="muted">승인 전송 후에는 최신 상태를 확인합니다. 새 버전은 다시 검토하고 승인해야 합니다.</p>
        </template>
        <p v-if="notice" role="status" class="callout success">{{ notice }}</p><p v-if="errors.decision" role="alert" class="callout warning">{{ errors.decision }}</p>
      </section>

    </template>
    <StationDetail v-if="loaded && !plan && selectedStation" :id="selectedStation" :stations="stationMap" :plan="plan" :horizon="loadedQuery?.horizonH ?? 3" />
    <section v-if="loaded" class="panel" aria-labelledby="stations-heading"><div class="section-heading"><h2 id="stations-heading">권역 대여소 현황</h2><span class="muted">{{ filteredStations.length }} / {{ stations.length }}곳</span></div>
        <div v-if="filteredStations.length" class="table-scroll" tabindex="0" aria-label="권역 대여소 표"><table><thead><tr><th>대여소</th><th>현재 / 거치대</th><th>{{ loadedQuery?.horizonH }}시간 후</th><th>위험</th><th>관측 (한국 시간)</th></tr></thead><tbody><tr v-for="s in filteredStations" :key="s.stationId" :class="{ focused: selectedStation === s.stationId }"><td><button class="station-button" :aria-label="`${s.name} 상세`" @click="selectedStation = s.stationId">{{ s.name }}</button><span class="small-line">{{ s.stationId }}</span></td><td>{{ numberText(s.bikes) }} / {{ numberText(s.docks) }}</td><td>{{ numberText(s.forecastBikes) }}</td><td><span class="risk-dot" :class="s.risk" aria-hidden="true"></span>{{ label(riskLabels, s.risk) }}</td><td>{{ timeText(s.observedAt) }}</td></tr></tbody></table></div>
        <p v-else class="empty-inline">{{ stations.length ? '검색 조건에 맞는 대여소가 없습니다.' : '표시할 대여소 현황이 없습니다.' }}</p>
    </section>
  </div>
</template>

<style src="./plan-review.css"></style>
