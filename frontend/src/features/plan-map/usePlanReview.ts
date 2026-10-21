import { computed, onScopeDispose, reactive, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from '../../shared/auth/store'
import { useTaskStream } from '../../shared/composables/useTaskStream'
import { decisionBody } from '../approval/approval'
import { apiSource } from './source'
import { createDemoSource, DEMO_REGION, DEMO_SHIFT, DEMO_TASK_ID } from './demo'
import { approvalBlockReason, editableStop, isTaskId, ReviewError, validateQuery, type Plan, type Query, type Station, type Task } from './model.ts'

export function usePlanReview(isDemo: () => boolean) {
  const auth = useAuth()
  const route = useRoute()
  const canRead = computed(() => isDemo() || ['MANAGER', 'EXECUTIVE'].includes(auth.user?.role ?? ''))
  const manager = computed(() => isDemo() || auth.user?.role === 'MANAGER')
  const query = reactive<Query>({ regionId: DEMO_REGION, shiftId: '', horizonH: 3 })
  const loadedQuery = ref<Query | null>(null)
  const plan = ref<Plan | null>(null)
  const stations = ref<Station[]>([])
  const task = ref<Task | null>(null)
  const taskInput = ref('')
  const linkedId = ref('')
  const loading = ref(false)
  const sending = ref(false)
  const loaded = ref(false)
  const errors = reactive({ plan: '', stations: '', task: '', decision: '', query: '' })
  const notice = ref('')
  const selectedTruck = ref('')
  const selectedStation = ref('')
  const excluded = ref<string[]>([])
  const reviewed = ref(false)
  const comment = ref('')
  const stationMap = computed(() => new Map(stations.value.map(s => [s.stationId, s])))
  const selectedRoute = computed(() => plan.value?.routes.find(r => r.truckId === selectedTruck.value))
  const sortedStops = computed(() => [...(selectedRoute.value?.stops ?? [])].sort((a, b) => a.seq - b.seq))
  const eligible = computed(() => new Set(plan.value?.routes.flatMap(r => r.stops.filter(editableStop).map(s => s.stationId)) ?? []))
  const changedQuery = computed(() => !!loadedQuery.value && JSON.stringify(query) !== JSON.stringify(loadedQuery.value))
  const signature = computed(() => `${plan.value?.planId}:${plan.value?.version}:${task.value?.pendingApproval?.approvalId}`)
  const sent = new Set<string>()
  const blockReason = computed(() => {
    if (loading.value || sending.value) return '최신 상태를 확인하고 있습니다.'
    if (changedQuery.value) return '조회 조건이 바뀌었습니다. 계획을 다시 조회하세요.'
    const reason = approvalBlockReason(plan.value, task.value, loadedQuery.value?.shiftId ?? '', manager.value)
    return reason || (sent.has(task.value?.pendingApproval?.approvalId ?? '') ? '결정을 전송한 요청입니다. 최신 작업 상태를 확인하세요.' : '')
  })
  let source = apiSource
  let controller: AbortController | undefined
  let decisionController: AbortController | undefined
  let generation = 0
  let contextEpoch = 0
  let timer: ReturnType<typeof setTimeout> | undefined
  const streamId = computed(() => !isDemo() && canRead.value && task.value && !['DONE', 'FAILED'].includes(task.value.status) ? task.value.taskId : null)
  const stream = useTaskStream(streamId)
  const message = (error: unknown) => error instanceof Error ? error.message : '요청을 처리하지 못했습니다.'

  watch(signature, () => { excluded.value = []; reviewed.value = false; comment.value = '' }, { flush: 'sync' })
  watch(() => stream.events.value.at(-1), event => {
    if (event && ['APPROVAL_REQUIRED', 'TOOL_END', 'DONE', 'FAILED'].includes(event.type)) {
      clearTimeout(timer)
      timer = setTimeout(() => { void reload() }, 150)
    }
  })

  async function reload() {
    if (!canRead.value || !loadedQuery.value) return
    controller?.abort()
    controller = new AbortController()
    const current = ++generation
    const request = { ...loadedQuery.value }
    const id = linkedId.value
    loading.value = true
    errors.plan = ''; errors.stations = ''; errors.task = ''
    const results = await Promise.allSettled([
      source.readPlan(request.shiftId, controller.signal),
      source.readStations(request.regionId, request.horizonH, controller.signal),
      id ? source.readTask(id, controller.signal) : Promise.resolve(null),
    ])
    if (current !== generation) return
    const [p, s, t] = results
    plan.value = p.status === 'fulfilled' ? p.value : null
    stations.value = s.status === 'fulfilled' ? s.value : []
    if (p.status === 'rejected') errors.plan = message(p.reason)
    if (s.status === 'rejected') errors.stations = message(s.reason)
    if (t.status === 'rejected') { task.value = null; errors.task = message(t.reason) }
    else if (t.value && (t.value.taskId !== id || t.value.shiftId !== request.shiftId)) {
      task.value = null; errors.task = '작업 ID 또는 근무조가 조회한 계획과 다릅니다.'
    } else task.value = t.value
    if (!plan.value?.routes.some(r => r.truckId === selectedTruck.value)) selectedTruck.value = plan.value?.routes[0]?.truckId ?? ''
    const allIds = new Set([...stations.value.map(s => s.stationId), ...(plan.value?.routes.flatMap(r => r.stops.map(s => s.stationId)) ?? []), ...(plan.value?.unserved.map(s => s.stationId) ?? [])])
    if (!allIds.has(selectedStation.value)) selectedStation.value = selectedRoute.value?.stops[0]?.stationId ?? stations.value[0]?.stationId ?? ''
    excluded.value = excluded.value.filter(id => eligible.value.has(id))
    loading.value = false; loaded.value = true
  }

  async function search() {
    if (sending.value) return
    errors.query = validateQuery(query)
    if (errors.query || !canRead.value) return
    controller?.abort(); generation++; contextEpoch++
    plan.value = null; stations.value = []; task.value = null
    linkedId.value = isDemo() ? DEMO_TASK_ID : ''
    taskInput.value = linkedId.value
    notice.value = ''; errors.decision = ''; loaded.value = false
    loadedQuery.value = { regionId: query.regionId.trim(), shiftId: query.shiftId.trim(), horizonH: query.horizonH }
    Object.assign(query, loadedQuery.value)
    await reload()
  }
  async function linkTask() {
    if (sending.value) return
    if (!isTaskId(taskInput.value.trim())) { errors.task = '작업 ID는 UUID 형식으로 입력하세요.'; return }
    task.value = null; linkedId.value = taskInput.value.trim(); errors.decision = ''; notice.value = ''
    await reload()
  }
  function toggleExclude(id: string, checked: boolean) {
    if (blockReason.value || !eligible.value.has(id)) return
    excluded.value = checked ? [...new Set([...excluded.value, id])] : excluded.value.filter(value => value !== id)
    reviewed.value = false
  }
  async function decide(kind: 'approve' | 'exclude' | 'reject') {
    const approvalId = task.value?.pendingApproval?.approvalId
    if (blockReason.value || !approvalId || !task.value || comment.value.length > 500) return
    if (kind === 'approve' && (!reviewed.value || excluded.value.length)) return
    if (kind === 'exclude' && !excluded.value.length) return
    const current = contextEpoch
    sending.value = true; errors.decision = ''; notice.value = ''; sent.add(approvalId)
    decisionController = new AbortController()
    try {
      await source.decide(task.value.taskId, decisionBody(task.value.pendingApproval!, kind === 'approve' ? 'APPROVE' : 'REJECT', {
        exclude: kind === 'exclude' ? [...excluded.value] : [], comment: comment.value,
      }), decisionController.signal)
      if (current !== contextEpoch) return
      notice.value = kind === 'exclude' ? '제외 요청을 전송했습니다. 새 후보가 도착하면 다시 검토하고 최종 승인하세요.' : '결정을 전송했습니다. 실제 반영 상태는 최신 계획과 작업에서 확인하세요.'
      reviewed.value = false
    } catch (error) {
      if (current !== contextEpoch) return
      // A missing response can still mean the server accepted the decision. Never resend automatically.
      errors.decision = error instanceof ReviewError && error.status === 409
        ? '이미 처리됐거나 오래된 승인 요청입니다. 최신 상태를 확인하세요.'
        : `${message(error)} 전송 결과를 작업에서 확인한 뒤 진행하세요.`
    } finally {
      if (current === contextEpoch) { sending.value = false; await reload() }
    }
  }
  function reset() {
    controller?.abort(); decisionController?.abort(); generation++; contextEpoch++; clearTimeout(timer)
    stream.close(); task.value = null; linkedId.value = ''; loadedQuery.value = null
    source = isDemo() ? createDemoSource() : apiSource
    sent.clear(); excluded.value = []; reviewed.value = false; comment.value = ''
    plan.value = null; stations.value = []; loaded.value = false; loading.value = false; sending.value = false
    Object.keys(errors).forEach(key => { errors[key as keyof typeof errors] = '' }); notice.value = ''
    query.regionId = isDemo() ? DEMO_REGION : typeof route.query.regionId === 'string' ? route.query.regionId : DEMO_REGION
    query.shiftId = isDemo() ? DEMO_SHIFT : typeof route.query.shiftId === 'string' ? route.query.shiftId : ''
    query.horizonH = 3; taskInput.value = ''
    if (canRead.value && query.shiftId) void search()
  }
  watch([isDemo, canRead], reset, { immediate: true })
  onScopeDispose(() => { generation++; contextEpoch++; controller?.abort(); decisionController?.abort(); clearTimeout(timer) })
  return { canRead, manager, query, loadedQuery, plan, stations, stationMap, task, taskInput, loading, sending, loaded, errors, notice,
    selectedTruck, selectedStation, selectedRoute, sortedStops, eligible, excluded, reviewed, comment, blockReason, changedQuery,
    streamState: stream.state, search, reload, linkTask, toggleExclude, decide, reset }
}
