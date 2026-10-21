// 아이소메트릭(비스듬히 내려다본 3D) 그림 도우미. 3D 관제 뷰와 트럭 적재 그림이 같이 쓴다.
// 시점은 +x, +y 쪽에서 내려다본다. 그래서 상자의 x+w 면(오른쪽)과 y+d 면(왼쪽)이 보이고, 북쪽(-y)은 화면 오른쪽 위다.

export type P3 = [number, number, number]
export type Faces = { top: string; left: string; right: string }

export function isoProjector(unit: number, ox: number, oy: number) {
  const C = unit * Math.cos(Math.PI / 6)
  const S = unit / 2

  function xy(x: number, y: number, z = 0): [number, number] {
    return [ox + (x - y) * C, oy + (x + y) * S - z * unit]
  }
  function pt(x: number, y: number, z = 0) {
    const [a, b] = xy(x, y, z)
    return `${a.toFixed(1)},${b.toFixed(1)}`
  }
  function poly(...ps: P3[]) {
    return ps.map((p) => pt(...p)).join(' ')
  }
  /** 바닥(z)에 깔린 사각형 */
  function flat(x: number, y: number, w: number, d: number, z = 0) {
    return poly([x, y, z], [x + w, y, z], [x + w, y + d, z], [x, y + d, z])
  }
  function box(x: number, y: number, z: number, w: number, d: number, h: number): Faces {
    const t = z + h
    return {
      top: flat(x, y, w, d, t),
      left: poly([x, y + d, z], [x + w, y + d, z], [x + w, y + d, t], [x, y + d, t]),
      right: poly([x + w, y, z], [x + w, y + d, z], [x + w, y + d, t], [x + w, y, t]),
    }
  }
  /** 땅 위에 눕혀 쓴 글자 (+x 방향으로 읽힌다) */
  function groundText(x: number, y: number, z = 0) {
    const [a, b] = xy(x, y, z)
    return `matrix(0.866 0.5 -0.866 0.5 ${a.toFixed(1)} ${b.toFixed(1)})`
  }
  /** 상자 왼쪽 면(y+d 면)에 세워 쓴 글자 */
  function sideText(x: number, y: number, z = 0) {
    const [a, b] = xy(x, y, z)
    return `matrix(0.866 0.5 0 1 ${a.toFixed(1)} ${b.toFixed(1)})`
  }

  return { C, S, xy, pt, poly, flat, box, groundText, sideText }
}

/** 위험 종류별 기둥 색 [윗면, 왼쪽 면, 오른쪽 면, 테두리] */
export const riskShades = {
  EMPTY: ['#fca5a5', '#ef4444', '#dc2626', '#b91c1c'],
  OVERFLOW: ['#fcd34d', '#f59e0b', '#d97706', '#b45309'],
  NONE: ['#6ee7b7', '#10b981', '#059669', '#047857'],
} as const
