import { onScopeDispose, ref, shallowRef } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import type { ApprovalDecision } from '../approval/approval'
import { cardOf, type LiveCard } from '../dashboard/feed.ts'

type DriverRoute = Schemas['DriverRoute']
type Station = Schemas['StationStatus']
type ReportBody = Schemas['FieldReportRequest']

/** 경로·변경 제안을 다시 읽는 간격. 재경로 카드와 승인 뒤 바뀐 경로가 이 안에 보인다 */
const POLL_MS = 10_000

/**
 * 기사 앱 자료 (내 트럭 하나).
 * - 경로: GET /api/drivers/me/route (DriverRoute: 오늘 ACTIVE 계획의 내 경로 + 근무조). 없으면 404라 null.
 *   정류장 진행(DONE·SKIPPED)은 현장 보고가 backend에 남긴다. 화면은 경로를 다시 읽기만 한다.
 * - 대여소: 경로 근무조의 구역(근무조 ID 앞부분) 대여소 현황. 길안내 좌표와 보고 시트의 앱 재고에 쓴다.
 * - 변경 제안: 승인 대기 작업 목록(GET /api/ai/tasks?status=WAITING_APPROVAL). 기사에게는 자기 트럭이 걸린 승인 카드가 있는 작업만
 *   나온다. 기사가 결정할 카드(approver=DRIVER)만 보이고, 관리자 승인을 먼저 받는 큰 변경은 수만 센다.
 */
export function useDriver(truckId: () => string) {
  const route = ref<DriverRoute | null>(null)
  const routeState = ref<'loading' | 'ready' | 'error'>('loading')
  const routeError = ref('')
  const stations = shallowRef(new Map<string, Station>())
  const cards = ref<LiveCard[]>([])
  /** 관리자 승인을 기다리는 내 트럭 변경 (큰 변경의 첫 카드) */
  const waitingManager = ref(0)
  let stationsRegion = ''

  async function loadRoute() {
    try {
      const { data, error, response } = await api.GET('/api/drivers/me/route')
      if (response.status === 404) {
        route.value = null
      } else if (error || !data) {
        routeError.value = problemMessage(error)
        routeState.value = 'error'
        return
      } else {
        route.value = data
        void loadStations(data.shiftId.split(':')[0]!)
      }
      routeState.value = 'ready'
    } catch (e) {
      routeError.value = e instanceof Error ? e.message : String(e)
      routeState.value = 'error'
    }
  }

  /** 구역이 바뀌었거나 아직 못 읽었을 때만 읽는다. 실패해도 경로는 그대로 보인다 (이름 검색 길안내, 앱 재고 0) */
  async function loadStations(regionId: string) {
    if (regionId === stationsRegion && stations.value.size) return
    const { data } = await api.GET('/api/regions/{regionId}/stations', { params: { path: { regionId }, query: { horizonH: 3 } } })
    if (!data) return
    stationsRegion = regionId
    stations.value = new Map(data.map((s) => [s.stationId, s]))
  }

  async function loadProposals() {
    const { data } = await api.GET('/api/ai/tasks', { params: { query: { status: 'WAITING_APPROVAL', size: 50 } } })
    if (!data) return
    const mine = data.items.map(cardOf).filter((c): c is LiveCard => !!c && (c.diff?.truckId ?? c.truckId) === truckId())
    cards.value = mine.filter((c) => c.approver === 'DRIVER')
    waitingManager.value = mine.length - cards.value.length
  }

  function refresh() {
    void loadRoute()
    void loadProposals()
  }

  /** POST /api/reports. 받아들여지면 재경로 작업 ID(issue가 있을 때), 아니면 오류 문장을 던진다 */
  async function report(body: ReportBody): Promise<string | null> {
    const { data, error } = await api.POST('/api/reports', { body })
    if (error || !data) throw new Error(problemMessage(error))
    refresh()
    return data.taskId ?? null
  }

  /** POST /api/ai/tasks/{taskId}/approval. 받아들여지면 카드를 내리고, 거부되면(409 이미 처리, 403 승인 주체 아님) 이유를 던진다 */
  async function decide(card: LiveCard, body: ApprovalDecision) {
    const { error, response } = await api.POST('/api/ai/tasks/{taskId}/approval', { params: { path: { taskId: card.taskId } }, body })
    if (error || !response.ok) throw new Error(problemMessage(error))
    cards.value = cards.value.filter((c) => c.approvalId !== card.approvalId)
    refresh()
  }

  refresh()
  const timer = window.setInterval(refresh, POLL_MS)
  onScopeDispose(() => window.clearInterval(timer))

  return { route, routeState, routeError, stations, cards, waitingManager, refresh, report, decide }
}
