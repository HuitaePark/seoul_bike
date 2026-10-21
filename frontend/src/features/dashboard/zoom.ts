// 지도(SVG) 확대·축소·이동 규칙. 보기는 viewBox(그림 좌표의 사각형)다. 화면·DOM 없이 node --test로 시험한다.

export type View = { x: number; y: number; w: number; h: number }
export type Box = { minX: number; minY: number; maxX: number; maxY: number }

/** 가장 크게 확대할 수 있는 배수 (처음 보기 대비) */
export const MAX_ZOOM = 8

/** 내용 전체를 여백과 함께 담는 처음 보기 */
export function fit(b: Box, margin: number): View {
  return { x: b.minX - margin, y: b.minY - margin, w: b.maxX - b.minX + margin * 2, h: b.maxY - b.minY + margin * 2 }
}

/** 처음 보기 대비 확대 배수 (1 = 전체 보기) */
export const zoomOf = (v: View, home: View) => home.w / v.w

/** 보기 가운데가 처음 보기 밖으로 나가지 않게 한다 (끌어서 내용을 다 밀어내지 못한다) */
export function clamp(v: View, home: View): View {
  const cx = Math.min(Math.max(v.x + v.w / 2, home.x), home.x + home.w)
  const cy = Math.min(Math.max(v.y + v.h / 2, home.y), home.y + home.h)
  return { ...v, x: cx - v.w / 2, y: cy - v.h / 2 }
}

/**
 * (px, py) 지점을 화면에서 그대로 둔 채 factor배 확대(>1)·축소(<1)한다. 마우스 휠은 커서 아래가 고정된다.
 * 축소는 처음 보기까지, 확대는 MAX_ZOOM배까지다.
 */
export function zoomAt(v: View, factor: number, px: number, py: number, home: View): View {
  const w = Math.min(Math.max(v.w / factor, home.w / MAX_ZOOM), home.w)
  const s = w / v.w
  if (s === 1) return v
  if (w === home.w) return home
  return clamp({ x: px - (px - v.x) * s, y: py - (py - v.y) * s, w, h: v.h * s }, home)
}

/** 그림 좌표로 dx, dy만큼 내용을 민다 (끌면 내용이 손을 따라온다) */
export function panBy(v: View, dx: number, dy: number, home: View): View {
  return clamp({ ...v, x: v.x - dx, y: v.y - dy }, home)
}

/** 휠 한 번의 확대 배수. 줄 단위(deltaMode 1)·쪽 단위(2) 휠도 픽셀로 맞추고, 한 번에 너무 크게 바뀌지 않게 자른다 */
export function wheelFactor(deltaY: number, deltaMode = 0): number {
  const px = deltaY * (deltaMode === 1 ? 16 : deltaMode === 2 ? 400 : 1)
  return Math.exp(-Math.min(Math.max(px, -300), 300) * 0.0015)
}
