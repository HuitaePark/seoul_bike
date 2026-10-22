import { test } from 'node:test'
import assert from 'node:assert/strict'
import { splitBlocks } from '../src/shared/composables/sse.ts'
import {
  clock, emptyFilter, isProblem, mergeTraces, parseCommand, parseTraceBlock, rationales, summarize, visible, type AgentTrace,
} from '../src/features/agent-log/agentLog.ts'

const TASK_A = '3530ad18-b534-4ab7-a636-94656f049298'
const TASK_B = '7c1e2f30-1111-4ab7-a636-94656f049298'

const trace = (id: number, kind: AgentTrace['kind'], extra: Partial<AgentTrace> = {}): AgentTrace =>
  ({
    id, eventId: `e${id}`, taskId: TASK_A, shiftId: 'N02:2026-10-22:AM', source: 'HARNESS', kind, turn: null,
    title: `${kind} ${id}`, detail: {}, occurredAt: `2026-10-22T07:0${id % 10}:00+09:00`, receivedAt: '2026-10-22T07:00:00+09:00',
    ...extra,
  }) as AgentTrace

const decision = (id: number, rationale: string | null, taskId = TASK_A) =>
  trace(id, 'DECISION', {
    taskId,
    turn: 1,
    title: '선택: business_request_route_plan(kind=REPLAN)',
    detail: { calls: [{ id: 'c1', name: 'business_request_route_plan', args: { kind: 'REPLAN' }, rationale }] },
  })

test('새 기록은 id 순서로 합치고 같은 id는 한 번만, 한도를 넘으면 오래된 것부터 버린다', () => {
  const merged = mergeTraces([trace(2, 'TOOL_CALL'), trace(5, 'RUN_END')], [trace(3, 'TOOL_RESULT'), trace(5, 'RUN_END')])
  assert.deepEqual(merged.map((t) => t.id), [2, 3, 5])
  assert.deepEqual(mergeTraces(merged, [trace(9, 'ROUTE')], 3).map((t) => t.id), [3, 5, 9])
  const same = [trace(1, 'ROUTE')]
  assert.equal(mergeTraces(same, []), same)
})

test('판단 줄은 도구마다 모델이 적은 이유를 보여 주고, 이유가 없는 호출은 뺀다', () => {
  assert.deepEqual(rationales(decision(1, '진입 불가 보고라 TR-03만 다시 계산한다')), [
    { name: 'business_request_route_plan', rationale: '진입 불가 보고라 TR-03만 다시 계산한다' },
  ])
  assert.deepEqual(rationales(decision(2, null)), [])
  assert.deepEqual(rationales(trace(3, 'TOOL_CALL', { detail: { rationale: '다른 줄' } })), [])
})

test('작업·종류·글자로 거른다. 글자는 제목과 모델의 이유 모두에서 찾는다', () => {
  const all = [
    trace(1, 'RUN_START'), decision(2, '진입 불가 보고라 다시 계산'), trace(3, 'TOOL_CALL'),
    trace(4, 'GUARD', { detail: { reasons: ['조회 결과에 없는 대여소 ID ST-9999'] } }), decision(5, '다른 작업', TASK_B),
  ]
  assert.equal(visible(all, emptyFilter()).length, 5)
  assert.deepEqual(visible(all, { ...emptyFilter(), taskId: TASK_B }).map((t) => t.id), [5])
  assert.deepEqual(visible(all, { ...emptyFilter(), groups: ['decision'] }).map((t) => t.id), [2, 5])
  assert.deepEqual(visible(all, { ...emptyFilter(), groups: ['tool', 'guard'] }).map((t) => t.id), [3, 4])
  assert.deepEqual(visible(all, { ...emptyFilter(), text: '진입 불가' }).map((t) => t.id), [2])
  assert.deepEqual(visible(all, { ...emptyFilter(), text: 'st-9999' }).map((t) => t.id), [4])
})

test('작업 목록은 최근 작업부터, 상태는 마지막 끝·승인 대기 줄로 정한다', () => {
  const tasks = summarize([
    trace(1, 'RUN_START', { detail: { taskType: 'field_report' }, occurredAt: '2026-10-22T07:00:00+09:00' }),
    decision(2, '이유'),
    trace(3, 'APPROVAL_WAIT', { occurredAt: '2026-10-22T07:00:05+09:00' }),
    trace(4, 'RUN_START', { taskId: TASK_B, detail: { taskType: 'qa' }, occurredAt: '2026-10-22T07:01:00+09:00' }),
    trace(5, 'RUN_END', { taskId: TASK_B, detail: { status: 'FAILED' }, occurredAt: '2026-10-22T07:01:09+09:00' }),
  ])
  assert.deepEqual(tasks.map((t) => [t.taskId, t.taskType, t.status, t.decisions]), [
    [TASK_B, 'qa', 'FAILED', 0],
    [TASK_A, 'field_report', 'WAITING_APPROVAL', 1],
  ])
})

test('시각은 칸 폭이 같은 HH:MM:SS, 읽을 수 없는 값은 그대로', () => {
  assert.match(clock('2026-10-22T07:05:09+09:00'), /^\d{2}:\d{2}:\d{2}$/)
  assert.equal(clock('언제'), '언제')
})

test('오류 결과·실패로 끝난 안전장치·실패한 실행은 붉게 보인다', () => {
  assert.equal(isProblem(trace(1, 'TOOL_RESULT', { detail: { ok: false } })), true)
  assert.equal(isProblem(trace(2, 'TOOL_RESULT', { detail: { ok: true } })), false)
  assert.equal(isProblem(trace(3, 'GUARD', { detail: { action: 'RETRY' } })), false)
  assert.equal(isProblem(trace(4, 'GUARD', { detail: { action: 'FAIL' } })), true)
  assert.equal(isProblem(trace(5, 'RUN_END', { detail: { status: 'FAILED' } })), true)
})

test('관리자 로그 SSE 블록을 읽는다. 하트비트·깨진 JSON·id 없는 data는 기록이 아니다', () => {
  const body = `: ping\n\nid:42\nevent:DECISION\ndata:${JSON.stringify(decision(42, '이유'))}\n\nid:43\nevent:TOOL`
  const { blocks, rest } = splitBlocks(body)
  assert.equal(rest, 'id:43\nevent:TOOL')
  const parsed = blocks.map(parseTraceBlock)
  assert.equal(parsed[0], null)
  assert.equal(parsed[1]?.id, 42)
  assert.equal(parsed[1]?.kind, 'DECISION')
  assert.equal(parseTraceBlock('data: {"id":'), null)
  assert.equal(parseTraceBlock('data: {"title":"id 없음"}'), null)
})

test('명령 줄: 작업 ID 앞부분을 풀고, 모르는 명령·종류는 알려 준다', () => {
  const known = [TASK_A, TASK_B]
  assert.deepEqual(parseCommand('task 3530', known), { type: 'task', taskId: TASK_A })
  assert.deepEqual(parseCommand('task all', known), { type: 'task', taskId: null })
  assert.equal(parseCommand('task ffff', known).type, 'error')
  assert.deepEqual(parseCommand('kind decision,tool', known), { type: 'kind', groups: ['decision', 'tool'] })
  assert.equal(parseCommand('kind thinking', known).type, 'error')
  assert.deepEqual(parseCommand('grep 진입 불가', known), { type: 'grep', text: '진입 불가' })
  assert.deepEqual(parseCommand('verbose off', known), { type: 'verbose', on: false })
  assert.equal(parseCommand('PAUSE', known).type, 'pause')
  assert.equal(parseCommand('resume', known).type, 'follow')
  assert.equal(parseCommand('rm -rf', known).type, 'error')
})
