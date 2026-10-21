import { test } from 'node:test'
import assert from 'node:assert/strict'
import { parseBlock, splitBlocks } from '../src/shared/composables/sse.ts'
import { cardAfter, cardOf, lineAfter, minutesAgo, pickStreams, sortCards, type Line, type LiveCard } from '../src/features/dashboard/feed.ts'
import { slotAt } from '../src/shared/region/regions.ts'
import type { Schemas } from '../src/shared/api/client'

type TaskEvent = Schemas['TaskEvent']
type Task = Schemas['Task']

const TASK = '3530ad18-b534-4ab7-a636-94656f049298'
let seq = 0
const ev = (type: TaskEvent['type'], extra: Partial<TaskEvent> = {}): TaskEvent =>
  ({ taskId: TASK, seq: ++seq, type, occurredAt: '2026-10-21T06:01:03Z', ...extra }) as TaskEvent
const approval = (approvalId: string, approver: 'MANAGER' | 'DRIVER' = 'MANAGER') =>
  ({ approvalId, kind: 'ROUTE_CHANGE', approver, summary: `truck-1 경로 변경 (${approvalId})` }) as Schemas['ApprovalRequest']
const task = (taskId: string, status: Task['status'], createdAt: string, pendingApproval?: Schemas['ApprovalRequest']): Task =>
  ({ taskId, type: 'REQUEST', status, shiftId: 's', createdBy: 'm', createdAt, updatedAt: createdAt, pendingApproval }) as Task

test('SSE 조각을 빈 줄 단위로 자르고 덜 온 끝은 남긴다', () => {
  const { blocks, rest } = splitBlocks('id:1\nevent:TOKEN\ndata:{"seq":1}\n\nid:2\nevent:TO')
  assert.deepEqual(blocks, ['id:1\nevent:TOKEN\ndata:{"seq":1}'])
  assert.equal(rest, 'id:2\nevent:TO')
  assert.equal(splitBlocks('a\r\n\r\nb\r\n\r\n').blocks.length, 2)
})

test('Spring SseEmitter 형식(data: 뒤 공백 없음)과 공백 있는 형식을 같이 읽는다', () => {
  const spring = 'id:5\nevent:TOKEN\ndata:{"taskId":"t","seq":5,"type":"TOKEN","token":"[가짜 "}'
  assert.equal(parseBlock(spring)?.token, '[가짜 ')
  assert.equal(parseBlock('data: {"seq":6,"type":"DONE"}')?.type, 'DONE')
  // 여러 줄 data는 줄바꿈으로 잇는다
  assert.equal(parseBlock('data:{"seq":7,\ndata:"type":"TOKEN"}')?.seq, 7)
})

test('하트비트 주석, data 없는 블록, 깨진 JSON은 이벤트가 아니다', () => {
  assert.equal(parseBlock(':ping'), null)
  assert.equal(parseBlock('id:3\nevent:TOKEN'), null)
  assert.equal(parseBlock('data:{"seq":'), null)
})

test('승인 요청이 오면 카드가 생기고, 그 뒤 다른 이벤트가 오면(결정 뒤 재개) 내려간다', () => {
  let card: LiveCard | null = null
  card = cardAfter(card, TASK, ev('TOOL_START'))
  assert.equal(card, null)
  card = cardAfter(card, TASK, ev('APPROVAL_REQUIRED', { approval: approval('a1') }))
  assert.equal(card?.approvalId, 'a1')
  assert.equal(card?.taskId, TASK)
  // 큰 변경: 관리자 카드 다음 기사 카드로 바뀐다
  card = cardAfter(card, TASK, ev('APPROVAL_REQUIRED', { approval: approval('a2', 'DRIVER') }))
  assert.equal(card?.approver, 'DRIVER')
  assert.equal(cardAfter(card, TASK, ev('TOKEN', { token: '승인 순서를 확인했다' })), null)
  assert.equal(cardAfter(approval('a3') as LiveCard, TASK, ev('DONE')), null)
})

test('목록의 작업은 승인을 기다릴 때만 카드가 된다', () => {
  assert.equal(cardOf(task('t1', 'WAITING_APPROVAL', '2026-10-21T06:00:00Z', approval('a1')))?.approvalId, 'a1')
  assert.equal(cardOf(task('t2', 'RUNNING', '2026-10-21T06:00:00Z', approval('a1'))), null)
  assert.equal(cardOf(task('t3', 'WAITING_APPROVAL', '2026-10-21T06:00:00Z')), null)
})

test('묻기 답: 도구 이름을 보이다가 TOKEN을 이어 붙이고, DONE의 answer로 끝낸다', () => {
  const line: Line = { who: 'agent', text: '', state: 'working' }
  lineAfter(line, ev('TOOL_START', { tool: { name: 'business_get_risk_stations', label: '위험 대여소 조회 중' } }))
  assert.equal(line.tool, '위험 대여소 조회 중')
  lineAfter(line, ev('TOOL_END'))
  assert.equal(line.tool, undefined)
  lineAfter(line, ev('TOKEN', { token: '서울숲 ' }))
  lineAfter(line, ev('TOKEN', { token: '회수가 많다' }))
  assert.equal(line.text, '서울숲 회수가 많다')
  assert.equal(line.state, 'working')
  lineAfter(line, ev('DONE', { answer: '서울숲 정문이 넘칠 것으로 예측돼 회수가 많다.' }))
  assert.deepEqual([line.state, line.text], ['done', '서울숲 정문이 넘칠 것으로 예측돼 회수가 많다.'])
})

test('DONE에 answer가 없으면 받은 TOKEN을 답으로 남기고, FAILED는 오류로 끝낸다', () => {
  const a: Line = { who: 'agent', text: '', state: 'working' }
  lineAfter(a, ev('TOKEN', { token: '받은 만큼' }))
  lineAfter(a, ev('DONE'))
  assert.equal(a.text, '받은 만큼')
  const b: Line = { who: 'agent', text: '', state: 'working' }
  lineAfter(b, ev('FAILED', { error: 'LOOP_TIMEOUT' }))
  assert.deepEqual([b.state, b.text], ['failed', 'LOOP_TIMEOUT'])
})

test('결정 뒤 결과 줄: 다음 승인 단계를 알리고, 에이전트가 다시 움직이면 진행 중으로 돌아간다', () => {
  const line: Line = { who: 'agent', text: '결정을 넘겼다', state: 'working' }
  lineAfter(line, ev('APPROVAL_REQUIRED', { approval: approval('a2', 'DRIVER') }))
  assert.equal(line.state, 'done')
  assert.match(line.text, /^기사 수락을 기다린다: /)
  lineAfter(line, ev('TOKEN', { token: '승인 순서를 확인했다.' }))
  assert.deepEqual([line.state, line.text], ['working', '승인 순서를 확인했다.'])
})

test('다음 승인 요청이 와도 에이전트가 방금 말한 설명은 남긴다', () => {
  const line: Line = { who: 'agent', text: '', state: 'working' }
  lineAfter(line, ev('TOKEN', { token: '제약을 반영해 경로를 다시 짰다.' }))
  lineAfter(line, ev('APPROVAL_REQUIRED', { approval: approval('a3') }))
  assert.equal(line.text, '제약을 반영해 경로를 다시 짰다.\n→ 관리자 승인을 기다린다: truck-1 경로 변경 (a3)')
})

test('스트림은 내가 묻거나 결정한 작업 먼저, 그다음 최근 작업 순으로 상한까지 연다', () => {
  const open = [
    task('old', 'WAITING_APPROVAL', '2026-10-21T05:00:00Z'),
    task('new', 'RUNNING', '2026-10-21T06:00:00Z'),
    task('mid', 'QUEUED', '2026-10-21T05:30:00Z'),
    task('mine', 'RUNNING', '2026-10-21T04:00:00Z'),
  ]
  assert.deepEqual(pickStreams(open, new Set(['mine']), 3), ['mine', 'new', 'mid'])
  assert.deepEqual(pickStreams(open, new Set(), 10), ['new', 'mid', 'old', 'mine'])
})

test('카드는 최근 순, 몇 분 전은 0 아래로 내려가지 않는다', () => {
  const at = (since: string) => ({ ...approval(since), taskId: since, since }) as LiveCard
  assert.deepEqual(sortCards([at('2026-10-21T05:00:00Z'), at('2026-10-21T06:00:00+09:00'), at('2026-10-21T05:30:00Z')]).map((c) => c.since), [
    '2026-10-21T05:30:00Z',
    '2026-10-21T05:00:00Z',
    '2026-10-21T06:00:00+09:00',
  ])
  const now = Date.parse('2026-10-21T06:10:00Z')
  assert.equal(minutesAgo('2026-10-21T06:00:00Z', now), 10)
  assert.equal(minutesAgo('2026-10-21T06:20:00Z', now), 0)
})

test('근무 시간대는 서울 시각으로 고른다 (13시 전 오전, 19시 전 오후, 그 뒤 저녁)', () => {
  assert.equal(slotAt(new Date('2026-10-21T03:59:00Z')), 'AM') // 12:59 KST
  assert.equal(slotAt(new Date('2026-10-21T04:00:00Z')), 'PM') // 13:00 KST
  assert.equal(slotAt(new Date('2026-10-21T09:59:00Z')), 'PM') // 18:59 KST
  assert.equal(slotAt(new Date('2026-10-21T10:00:00Z')), 'EVE') // 19:00 KST
  assert.equal(slotAt(new Date('2026-10-20T17:00:00Z')), 'AM') // 다음 날 02:00 KST
})
