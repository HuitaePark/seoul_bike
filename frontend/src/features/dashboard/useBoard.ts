import { onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'

type Station = Schemas['StationStatus']
type Plan = Schemas['Plan']
type Safety = Schemas['ShiftSafety']

/** 대여소 재고는 수집기가 5분마다 넣는다(합의 12). 1분마다 읽으면 새 수집을 늦어도 1분 안에 본다 */
const STATIONS_MS = 60_000
/** 계획은 승인·재경로로 바뀐다. 승인 카드가 오가면 바로 다시 읽고, 그 밖에는 이 간격으로 읽는다 */
const PLAN_MS = 15_000

/**
 * 관제 대시보드의 지도·경로 자료 (구역 하나, 근무조 하나).
 * - 대여소: GET /api/regions/{regionId}/stations (실시간 재고 + 현장 보고 + 예측 위험). 재고가 아직 없는 대여소는 빠진다.
 * - 계획: GET /api/shifts/{shiftId}/plan. 그 근무조의 가장 높은 version이다 (ACTIVE만이 아니라 승인 대기 PROPOSED일 수도 있다).
 *   계획이 없으면 404라 null로 둔다.
 * - 무승인 변경 수: GET /api/shifts/{shiftId}/safety (PRD 9.2, 반드시 0). 계획과 같이 읽는다.
 * 읽기에 실패하면 마지막으로 받은 값을 그대로 두고 오류 문장만 바꾼다 (잠깐 끊겨도 지도가 비지 않게).
 */
export function useBoard(regionId: MaybeRefOrGetter<string>, shiftId: MaybeRefOrGetter<string>) {
  const stations = ref<Station[]>([])
  const plan = ref<Plan | null>(null)
  /** 못 읽었으면 null (화면은 안전 표시를 숨긴다. 모르는 값을 0으로 보이지 않는다) */
  const safety = ref<Safety | null>(null)
  /** 처음 읽기 전에는 'loading', 한 번이라도 받았으면 'ready' */
  const stationsState = ref<'loading' | 'ready'>('loading')
  const planState = ref<'loading' | 'ready'>('loading')
  const stationsError = ref('')
  const planError = ref('')
  let stationsCtl: AbortController | null = null
  let planCtl: AbortController | null = null

  async function refreshStations() {
    stationsCtl?.abort()
    const ctl = (stationsCtl = new AbortController())
    const id = toValue(regionId)
    try {
      const { data, error } = await api.GET('/api/regions/{regionId}/stations', {
        params: { path: { regionId: id }, query: { horizonH: 3 } },
        signal: ctl.signal,
      })
      if (ctl.signal.aborted) return
      if (error || !data) {
        stationsError.value = problemMessage(error)
        return
      }
      stations.value = data
      stationsError.value = ''
      stationsState.value = 'ready'
    } catch (e) {
      if (!ctl.signal.aborted) stationsError.value = e instanceof Error ? e.message : String(e)
    }
  }

  async function refreshPlan() {
    planCtl?.abort()
    const ctl = (planCtl = new AbortController())
    const id = toValue(shiftId)
    try {
      const { data, error, response } = await api.GET('/api/shifts/{shiftId}/plan', {
        params: { path: { shiftId: id } },
        signal: ctl.signal,
      })
      if (ctl.signal.aborted) return
      if (response.status === 404) {
        plan.value = null
      } else if (error || !data) {
        planError.value = problemMessage(error)
        return
      } else {
        plan.value = data.shiftId === id ? data : null
      }
      planError.value = ''
      planState.value = 'ready'
      const counts = await api.GET('/api/shifts/{shiftId}/safety', { params: { path: { shiftId: id } }, signal: ctl.signal })
      if (!ctl.signal.aborted) safety.value = counts.data?.shiftId === id ? counts.data : null
    } catch (e) {
      if (!ctl.signal.aborted) planError.value = e instanceof Error ? e.message : String(e)
    }
  }

  // 구역·근무조를 바꾸면 앞 근무조 자료를 지우고 다시 읽는다 (다른 근무조 계획이 잠깐이라도 보이지 않게)
  watch(
    () => toValue(regionId),
    () => {
      stations.value = []
      stationsState.value = 'loading'
      void refreshStations()
    },
    { immediate: true },
  )
  watch(
    () => toValue(shiftId),
    () => {
      plan.value = null
      safety.value = null
      planState.value = 'loading'
      void refreshPlan()
    },
    { immediate: true },
  )
  const timers = [window.setInterval(refreshStations, STATIONS_MS), window.setInterval(refreshPlan, PLAN_MS)]

  onScopeDispose(() => {
    timers.forEach((t) => window.clearInterval(t))
    stationsCtl?.abort()
    planCtl?.abort()
  })

  return { stations, plan, safety, stationsState, planState, stationsError, planError, refreshStations, refreshPlan }
}
