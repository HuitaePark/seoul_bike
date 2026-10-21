// SSE 글자 흐름을 작업 이벤트로 자르는 규칙. fetch·Vue와 떼어 두어 node --test로 시험한다.

import type { Schemas } from '../api/client'

export type TaskEvent = Schemas['TaskEvent']

/** 빈 줄로 끝난 블록만 꺼낸다. 아직 덜 온 끝부분은 rest로 남겨 다음 조각에 잇는다. */
export function splitBlocks(buffer: string): { blocks: string[]; rest: string } {
  const blocks = buffer.split(/\r?\n\r?\n/)
  const rest = blocks.pop() ?? ''
  return { blocks, rest }
}

/**
 * 블록 하나 → 작업 이벤트. 주석 줄(`: ping`)만 있거나 data가 없으면 null.
 * data 줄이 여럿이면 줄바꿈으로 잇고, `data:` 뒤 공백 하나는 뺀다 (SSE 규칙). JSON이 깨졌으면 그 블록만 버린다.
 */
export function parseBlock(block: string): TaskEvent | null {
  const data: string[] = []
  for (const line of block.split(/\r?\n/)) {
    if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
  }
  if (!data.length) return null
  try {
    return JSON.parse(data.join('\n')) as TaskEvent
  } catch {
    return null
  }
}
