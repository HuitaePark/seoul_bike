// 화면 스타일 규칙 (검증 루프: npm test, CI). 브라우저 없이 src의 .vue·.css 전체를 읽는다.
// 색 그림자 금지: 떨어지는 그림자(위치·번짐이 있는 box-shadow, drop-shadow(), SVG feDropShadow)는 중립색(슬레이트·검정·흰색)만 쓴다.
// 선택·포커스 고리(0 0 0 Npx, inset)는 그림자가 아니라 테두리라 색을 써도 된다.
import { test } from 'node:test'
import assert from 'node:assert/strict'
import { readdirSync, readFileSync } from 'node:fs'
import { join, relative } from 'node:path'

const SRC = new URL('../src/', import.meta.url).pathname

function files(dir: string): string[] {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? files(join(dir, e.name)) : /\.(vue|css)$/.test(e.name) ? [join(dir, e.name)] : [],
  )
}

const COLOR = /rgba?\([^)]*\)|hsla?\([^)]*\)|#[0-9a-f]{3,8}\b|color-mix\((?:[^()]|\([^)]*\))*\)|var\([^)]*\)|\b(?!inset\b)[a-z]+\b/i
// 중립색: 슬레이트 900(#0f172a = rgb 15 23 42), 검정, 흰색, 투명, 공통 그림자 토큰
const NEUTRAL = [
  /^rgba?\(\s*15[\s,]+23[\s,]+42\b/,
  /^rgba?\(\s*0[\s,]+0[\s,]+0\b/,
  /^rgba?\(\s*255[\s,]+255[\s,]+255\b/,
  /^#(0f172a|000|000000|fff|ffffff)([0-9a-f]{2})?$/i,
  /^(black|white|transparent)$/i,
  /^var\(--shadow\)$/,
]

/** 쉼표로 그림자 층을 나눈다 (괄호 안 쉼표는 건너뛴다) */
function layers(value: string): string[] {
  const out: string[] = []
  let depth = 0
  let cur = ''
  for (const ch of value) {
    if (ch === '(') depth++
    if (ch === ')') depth--
    if (ch === ',' && depth === 0) {
      out.push(cur)
      cur = ''
    } else cur += ch
  }
  return [...out, cur].map((l) => l.trim()).filter(Boolean)
}

/** 떨어지는 그림자의 색이 중립색이 아니면 그 색. 고리·inset·중립색·없음이면 null */
export function coloredDrop(layer: string): string | null {
  if (/^(none|var\(--shadow\))$/.test(layer) || /\binset\b/.test(layer)) return null
  const color = layer.match(COLOR)?.[0] ?? 'currentColor'
  const lengths = layer.replace(color, ' ').match(/-?\d*\.?\d+/g)?.map(Number) ?? []
  const [x = 0, y = 0, blur = 0] = lengths
  if (x === 0 && y === 0 && blur === 0) return null // 고리 (spread만 있다)
  return NEUTRAL.some((n) => n.test(color)) ? null : color
}

/** 파일 안의 그림자 선언: [줄 번호, 층] */
function shadows(text: string): [number, string][] {
  const out: [number, string][] = []
  text.split('\n').forEach((line, i) => {
    for (const m of line.matchAll(/box-shadow:\s*([^;}"]+)/g)) layers(m[1]!).forEach((l) => out.push([i + 1, l]))
    for (const m of line.matchAll(/drop-shadow\(((?:[^()]|\([^)]*\))*)\)/g)) out.push([i + 1, m[1]!.trim()])
    for (const m of line.matchAll(/<feDropShadow\b[^>]*flood-color="([^"]+)"/g)) out.push([i + 1, `0 1px 1px ${m[1]}`])
  })
  return out
}

test('색 그림자 판정: 색이 있는 떨어지는 그림자만 걸린다', () => {
  assert.equal(coloredDrop('0 4px 10px rgb(15 157 88 / 0.25)'), 'rgb(15 157 88 / 0.25)')
  assert.equal(coloredDrop('0 6px 16px #6366f1'), '#6366f1')
  assert.equal(coloredDrop('0 8px 24px rgb(15 23 42 / 0.12)'), null)
  assert.equal(coloredDrop('0 1px 5px #0f172a70'), null)
  assert.equal(coloredDrop('0 0 0 4px rgb(16 185 129 / 0.2)'), null) // 고리
  assert.equal(coloredDrop('inset 0 0 0 1px #6366f1'), null)
  assert.equal(coloredDrop('var(--shadow)'), null)
})

test('화면 소스에 색 그림자가 없다 (떨어지는 그림자는 중립색만)', () => {
  const found = files(SRC).flatMap((file) =>
    shadows(readFileSync(file, 'utf8')).flatMap(([line, layer]) => {
      const color = coloredDrop(layer)
      return color ? [`${relative(SRC, file)}:${line} ${layer}`] : []
    }),
  )
  assert.deepEqual(found, [], `색 그림자를 지우거나 중립색(rgb(15 23 42 / a))으로 바꾼다:\n${found.join('\n')}`)
})
