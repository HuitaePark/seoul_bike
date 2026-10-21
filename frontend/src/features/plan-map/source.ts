import { api, problemMessage } from '../../shared/api/client'
import { ReviewError, type PlanReviewSource } from './model.ts'

export const apiSource: PlanReviewSource = {
  async readPlan(shiftId, signal) {
    const { data, error, response } = await api.GET('/api/shifts/{shiftId}/plan', {
      params: { path: { shiftId } }, signal,
    })
    if (response.status === 404) return null
    if (error || !data) throw new ReviewError(problemMessage(error), response.status)
    if (data.shiftId !== shiftId) throw new ReviewError('요청한 근무조와 다른 계획이 반환됐습니다.')
    return data
  },
  async readStations(regionId, horizonH, signal) {
    const { data, error, response } = await api.GET('/api/regions/{regionId}/stations', {
      params: { path: { regionId }, query: { horizonH } }, signal,
    })
    if (error || !data) throw new ReviewError(problemMessage(error), response.status)
    return data
  },
  async readTask(taskId, signal) {
    const { data, error, response } = await api.GET('/api/ai/tasks/{taskId}', {
      params: { path: { taskId } }, signal,
    })
    if (error || !data) throw new ReviewError(problemMessage(error), response.status)
    return data
  },
  async decide(taskId, body, signal) {
    const { error, response } = await api.POST('/api/ai/tasks/{taskId}/approval', {
      params: { path: { taskId } }, body, signal,
    })
    if (error || !response.ok) throw new ReviewError(problemMessage(error), response.status)
  },
}
