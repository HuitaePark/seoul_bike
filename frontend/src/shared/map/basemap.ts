// 지도 화면(계획 검토, 관제 평면 지도)이 같이 쓰는 배경지도. 무료·키 없는 OpenFreeMap 스타일을 기본으로 쓴다.
// 스타일을 직접 받아 검사한 뒤 MapLibre에 넘긴다. 못 받으면 빈 바탕(emptyStyle) 위에 대여소·경로만 그린다.
import type { ExpressionSpecification, StyleSpecification } from 'maplibre-gl'

export const emptyStyle: StyleSpecification = { version: 8, sources: {}, layers: [{ id: 'plain', type: 'background', paint: { 'background-color': '#edf2f7' } }] }
export const basemapStyleUrl = import.meta.env.VITE_PLAN_MAP_STYLE_URL || 'https://tiles.openfreemap.org/styles/liberty'

function assetUrl(value: string, base: URL): string {
  return new URL(value, base).href.replace(/%7B/gi, '{').replace(/%7D/gi, '}')
}

/** 스타일을 받아 검사하고, 공급자 기준 상대 주소(스프라이트·글꼴·타일)를 절대 주소로 바꾼다. 실패하면 throw */
export async function fetchBasemap(signal: AbortSignal): Promise<StyleSpecification> {
  const url = new URL(basemapStyleUrl)
  if (!['https:', 'http:'].includes(url.protocol)) throw new Error('Unsupported style URL')
  const response = await fetch(url, { signal, credentials: 'omit' })
  if (!response.ok) throw new Error('Style request failed')
  const style = await response.json() as StyleSpecification
  if (style.version !== 8 || !style.sources || !Array.isArray(style.layers)) throw new Error('Invalid style')
  if (typeof style.sprite === 'string') style.sprite = assetUrl(style.sprite, url)
  else if (Array.isArray(style.sprite)) style.sprite = style.sprite.map(s => ({ ...s, url: assetUrl(s.url, url) }))
  if (style.glyphs) style.glyphs = assetUrl(style.glyphs, url)
  for (const source of Object.values(style.sources)) {
    if ('url' in source && typeof source.url === 'string') source.url = assetUrl(source.url, url)
    if ('tiles' in source && Array.isArray(source.tiles)) source.tiles = source.tiles.map(tile => assetUrl(tile, url))
  }
  return style
}

const KOREAN_NAME: ExpressionSpecification = ['coalesce', ['get', 'name:ko'], ['get', 'name:nonlatin'], ['get', 'name']]

/** OpenFreeMap 기본 글자는 "Gangnam-gu 강남구"처럼 로마자·한글을 같이 쓴다. 지명 글자(name:*)를 한글 하나로 바꾼다 */
export function koreanLabels(style: StyleSpecification): StyleSpecification {
  for (const layer of style.layers) {
    if (layer.type !== 'symbol') continue
    const field = layer.layout?.['text-field']
    if (field && JSON.stringify(field).includes('"name')) layer.layout!['text-field'] = KOREAN_NAME
  }
  return style
}
