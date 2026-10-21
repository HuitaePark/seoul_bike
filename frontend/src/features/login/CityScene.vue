<script setup lang="ts">
// 로그인 화면 오른쪽 일러스트. 도시 한 블록을 아이소메트릭으로 그려서
// "과잉 대여소 → 트럭 → 부족 대여소" 재배치 흐름과, 관제 센터에서 AI 계획을 사람이 승인하는 장면을 보여 준다.
// 그림은 장식이라 스크린 리더에는 숨긴다 (aria-hidden).

type P3 = [number, number, number]

const U = 30 // 격자 한 칸의 픽셀
const C = U * Math.cos(Math.PI / 6)
const S = U / 2
const OX = 404
const OY = 300

function xy(x: number, y: number, z = 0): [number, number] {
  return [OX + (x - y) * C, OY + (x + y) * S - z * U]
}
function pt(x: number, y: number, z = 0) {
  const [a, b] = xy(x, y, z)
  return `${a.toFixed(1)},${b.toFixed(1)}`
}
function poly(...ps: P3[]) {
  return ps.map((p) => pt(...p)).join(' ')
}
/** 바닥에 붙은 사각형 (x, y에서 w×d) */
function flat(x: number, y: number, w: number, d: number, z = 0) {
  return poly([x, y, z], [x + w, y, z], [x + w, y + d, z], [x, y + d, z])
}

const tones = {
  white: ['#ffffff', '#e4e9f0', '#c8d2df'],
  mint: ['#dff3e8', '#b3e0c8', '#8ccaa9'],
  sky: ['#e1ecf8', '#bad0ea', '#97b5dc'],
  sand: ['#fcefd9', '#f2d6aa', '#e3bc84'],
  brand: ['#22b871', '#0f9d58', '#0b7a44'],
} as const
type Tone = keyof typeof tones

/** 상자 하나의 보이는 세 면. 시점이 +x, +y 쪽이라 x+w 면(오른쪽)과 y+d 면(왼쪽)이 보인다. */
function box(x: number, y: number, z: number, w: number, d: number, h: number) {
  const t = z + h
  return {
    top: poly([x, y, t], [x + w, y, t], [x + w, y + d, t], [x, y + d, t]),
    left: poly([x, y + d, z], [x + w, y + d, z], [x + w, y + d, t], [x, y + d, t]),
    right: poly([x + w, y, z], [x + w, y + d, z], [x + w, y + d, t], [x + w, y, t]),
  }
}

/** 건물 벽의 창문 줄. 왼쪽 면은 x를 따라, 오른쪽 면은 y를 따라 늘어선다. */
function windows(x: number, y: number, w: number, d: number, h: number) {
  const out: string[] = []
  const rows = Math.floor((h - 0.5) / 0.7)
  for (let r = 0; r < rows; r++) {
    const z0 = 0.5 + r * 0.7
    for (let i = 0; i + 0.6 <= w - 0.2; i += 0.6) {
      const a = x + 0.25 + i
      out.push(poly([a, y + d, z0], [a + 0.3, y + d, z0], [a + 0.3, y + d, z0 + 0.38], [a, y + d, z0 + 0.38]))
    }
    for (let i = 0; i + 0.6 <= d - 0.2; i += 0.6) {
      const b = y + 0.25 + i
      out.push(poly([x + w, b, z0], [x + w, b + 0.3, z0], [x + w, b + 0.3, z0 + 0.38], [x + w, b, z0 + 0.38]))
    }
  }
  return out
}

interface Building { kind: 'building'; x: number; y: number; w: number; d: number; h: number; tone: Tone }
interface Tree { kind: 'tree'; x: number; y: number; r: number }
interface Station { kind: 'station'; x: number; y: number; bikes: number; docks: number; state: 'empty' | 'overflow' | 'ok' }
interface Truck { kind: 'truck'; x: number; y: number }
type Prop = Building | Tree | Station | Truck

const props: Prop[] = [
  // 북서: 관제 센터와 사무 건물
  { kind: 'building', x: 6, y: 0.6, w: 2, d: 2, h: 3, tone: 'sky' },
  { kind: 'building', x: 8.6, y: 1, w: 2, d: 2, h: 4.4, tone: 'white' },
  { kind: 'building', x: 6.2, y: 3.6, w: 1.6, d: 1.6, h: 2, tone: 'sand' },
  { kind: 'tree', x: 9.4, y: 4.6, r: 0.5 },
  { kind: 'tree', x: 0.9, y: 5, r: 0.45 },
  // 북동: 부족 대여소
  { kind: 'building', x: 13, y: 0.6, w: 2, d: 2, h: 5, tone: 'mint' },
  { kind: 'building', x: 16, y: 0.6, w: 3, d: 1.6, h: 3, tone: 'white' },
  { kind: 'building', x: 17.4, y: 3, w: 1.8, d: 1.8, h: 2.2, tone: 'sky' },
  { kind: 'station', x: 13.4, y: 3.9, bikes: 0, docks: 6, state: 'empty' },
  { kind: 'tree', x: 16.6, y: 4.8, r: 0.45 },
  // 남서: 과잉 대여소
  { kind: 'station', x: 2.6, y: 8.1, bikes: 9, docks: 6, state: 'overflow' },
  { kind: 'building', x: 7, y: 8.1, w: 2, d: 2, h: 3.4, tone: 'sand' },
  { kind: 'building', x: 0.6, y: 9.7, w: 1.6, d: 1.4, h: 1.8, tone: 'mint' },
  { kind: 'tree', x: 5.6, y: 10.6, r: 0.5 },
  { kind: 'tree', x: 9.9, y: 10.7, r: 0.45 },
  // 남동: 정상 대여소
  { kind: 'station', x: 13.8, y: 8.1, bikes: 4, docks: 6, state: 'ok' },
  { kind: 'building', x: 17.4, y: 8, w: 2, d: 2.6, h: 4, tone: 'white' },
  { kind: 'tree', x: 13.4, y: 10.6, r: 0.5 },
  { kind: 'tree', x: 15.6, y: 10.8, r: 0.4 },
  // 도로 위 재배치 트럭
  { kind: 'truck', x: 11.6, y: 6.2 },
]

// 앞에 있는 것(x+y가 큰 것)을 나중에 그린다
function depth(p: Prop) {
  if (p.kind === 'building') return p.x + p.w / 2 + p.y + p.d / 2
  if (p.kind === 'station') return p.x + 1.3 + p.y + 0.7
  return p.x + p.y
}
const sorted = [...props].sort((a, b) => depth(a) - depth(b))

const STATION_W = 2.6
const STATION_D = 1.4
const stateColor = { empty: '#ef4444', overflow: '#f59e0b', ok: '#10b981' }

function bikeSpots(s: Station) {
  const spots: [number, number][] = []
  for (let i = 0; i < s.bikes; i++) {
    const row = i < s.docks ? 0 : 1
    const col = i % s.docks
    spots.push(xy(s.x + 0.4 + col * 0.38, s.y + 0.55 + row * 0.5, 0.12))
  }
  return spots
}
function docks(s: Station) {
  return Array.from({ length: s.docks }, (_, i) => xy(s.x + 0.4 + i * 0.38, s.y + 0.5, 0.12))
}

const pins = props
  .filter((p): p is Station => p.kind === 'station')
  .map((s) => {
    const [x, y] = xy(s.x + STATION_W / 2, s.y + STATION_D / 2, 2.4)
    const [sx, sy] = xy(s.x + STATION_W / 2, s.y + STATION_D / 2, 0.13)
    return { x, y, sx, sy, color: stateColor[s.state], text: `${s.bikes}`, state: s.state }
  })

// 과잉 대여소에서 도로를 타고 부족 대여소로 가는 경로
const route = [pt(3.9, 8.1, 0.02), pt(3.9, 6.95, 0.02), pt(14.7, 6.95, 0.02), pt(14.7, 5.3, 0.02)].join(' ')

// 관제 센터: 오른쪽 벽엔 막대 차트, 왼쪽 벽엔 지도 화면
const tower = { x: 1.5, y: 1, w: 3, d: 3, h: 6.2 }
const towerBox = box(tower.x, tower.y, 0, tower.w, tower.d, tower.h)
const tx = tower.x + tower.w
const ty = tower.y + tower.d
const chartScreen = poly([tx, 1.3, 3.4], [tx, 3.7, 3.4], [tx, 3.7, 5.6], [tx, 1.3, 5.6])
const bars = [0.7, 1.4, 1.0, 1.7, 1.2].map((hh, i) => {
  const b = 1.55 + i * 0.42
  return {
    pts: poly([tx, b, 3.65], [tx, b + 0.24, 3.65], [tx, b + 0.24, 3.65 + hh], [tx, b, 3.65 + hh]),
    color: i === 3 ? '#f59e0b' : i === 1 ? '#ef4444' : '#22c55e',
  }
})
const mapScreen = poly([1.8, ty, 3.4], [4.2, ty, 3.4], [4.2, ty, 5.6], [1.8, ty, 5.6])
const mapRoute = [pt(2.2, ty, 3.8), pt(2.9, ty, 4.6), pt(3.5, ty, 4.2), pt(3.9, ty, 5.2)].join(' ')
const mapDots = [
  { p: xy(2.2, ty, 3.8), c: '#f59e0b' },
  { p: xy(2.9, ty, 4.6), c: '#22c55e' },
  { p: xy(3.9, ty, 5.2), c: '#ef4444' },
]
const door = poly([2.4, ty, 0], [3.2, ty, 0], [3.2, ty, 1.1], [2.4, ty, 1.1])
const roofBand = poly([tower.x, ty, 5.95], [tx, ty, 5.95], [tx, ty, 6.2], [tower.x, ty, 6.2])
const roofBandR = poly([tx, tower.y, 5.95], [tx, ty, 5.95], [tx, ty, 6.2], [tx, tower.y, 6.2])
const antennaBase = xy(3, 2.5, 6.2)
const antennaTop = xy(3, 2.5, 7.3)

// 트럭: 짐칸(초록) + 운전석(흰색), +x 방향으로 달린다
function truckParts(t: Truck) {
  return {
    cargo: box(t.x, t.y, 0.25, 1.9, 0.9, 1.05),
    cab: box(t.x + 1.9, t.y + 0.05, 0.25, 0.8, 0.8, 0.8),
    windshield: poly(
      [t.x + 2.7, t.y + 0.18, 0.62], [t.x + 2.7, t.y + 0.72, 0.62], [t.x + 2.7, t.y + 0.72, 0.96], [t.x + 2.7, t.y + 0.18, 0.96],
    ),
    sideWindow: poly(
      [t.x + 2.0, t.y + 0.85, 0.62], [t.x + 2.45, t.y + 0.85, 0.62], [t.x + 2.45, t.y + 0.85, 0.96], [t.x + 2.0, t.y + 0.85, 0.96],
    ),
    logo: xy(t.x + 0.95, t.y + 0.9, 0.85),
    wheels: [xy(t.x + 0.4, t.y + 0.9, 0.2), xy(t.x + 1.5, t.y + 0.9, 0.2), xy(t.x + 2.4, t.y + 0.85, 0.2)],
  }
}

const roadDashes = Array.from({ length: 13 }, (_, i) => {
  const a = 0.4 + i * 1.55
  return [pt(a, 6.7, 0.01), pt(a + 0.7, 6.7, 0.01)]
}).filter((_, i) => i !== 7) // 교차로 안의 점선은 뺀다
const waves = [
  [2, 12.5], [6.5, 13.2], [9.5, 12.4], [14.5, 13.1], [17.5, 12.5],
].map(([x, y]) => [pt(x, y), pt(x + 0.9, y)])
</script>

<template>
  <svg class="scene" viewBox="0 0 964 870" aria-hidden="true" focusable="false">
    <defs>
      <filter id="cardShadow" x="-20%" y="-20%" width="140%" height="160%">
        <feDropShadow dx="0" dy="6" stdDeviation="8" flood-color="#0f172a" flood-opacity="0.16" />
      </filter>
    </defs>

    <!-- 구름 -->
    <g class="cloud c1" fill="#fff">
      <ellipse cx="700" cy="70" rx="46" ry="16" /><circle cx="680" cy="60" r="18" /><circle cx="710" cy="52" r="22" />
    </g>
    <g class="cloud c2" fill="#fff" opacity="0.85">
      <ellipse cx="860" cy="170" rx="38" ry="13" /><circle cx="846" cy="161" r="14" /><circle cx="870" cy="155" r="17" />
    </g>
    <g class="cloud c3" fill="#fff" opacity="0.7">
      <ellipse cx="520" cy="130" rx="30" ry="10" /><circle cx="512" cy="123" r="11" /><circle cx="530" cy="119" r="13" />
    </g>

    <!-- 땅: 떠 있는 도시 한 블록 -->
    <polygon :points="poly([0, 14, 0], [20, 14, 0], [20, 14, -1.4], [0, 14, -1.4])" fill="#b89b7d" />
    <polygon :points="poly([20, 0, 0], [20, 14, 0], [20, 14, -1.4], [20, 0, -1.4])" fill="#9a7e63" />
    <polyline :points="[pt(0, 14, -0.5), pt(20, 14, -0.5), pt(20, 0, -0.5)].join(' ')" fill="none" stroke="#a88b6e" stroke-width="2" />
    <polygon :points="flat(0, 0, 20, 14)" fill="#cfe7c8" />

    <!-- 블록 바닥 -->
    <polygon :points="flat(0.3, 0.3, 10.4, 5.4)" fill="#eef5ec" />
    <polygon :points="flat(12.7, 0.3, 7, 5.4)" fill="#eef5ec" />
    <polygon :points="flat(0.3, 7.7, 10.4, 3.6)" fill="#eef5ec" />
    <polygon :points="flat(12.7, 7.7, 7, 3.6)" fill="#eef5ec" />

    <!-- 강과 다리 -->
    <polygon :points="flat(0, 11.6, 20, 2.4)" fill="#a5d6f2" />
    <polygon :points="poly([0, 14, 0], [20, 14, 0], [20, 14, -0.35], [0, 14, -0.35])" fill="#7fbfe4" />
    <polyline v-for="(w, i) in waves" :key="'w' + i" :points="w.join(' ')" stroke="#fff" stroke-width="2" stroke-linecap="round" opacity="0.8" />

    <!-- 도로 -->
    <polygon :points="flat(0, 6, 20, 1.4)" fill="#6b7686" />
    <polygon :points="flat(11, 0, 1.4, 11.6)" fill="#6b7686" />
    <polygon :points="flat(11, 6, 1.4, 1.4)" fill="#646f7f" />
    <polyline v-for="(d, i) in roadDashes" :key="'d' + i" :points="d.join(' ')" stroke="#f8fafc" stroke-width="2" opacity="0.7" />
    <template v-for="part in [box(11, 11.4, 0, 1.4, 2.8, 0.25)]" :key="'bridge'">
      <polygon :points="part.top" fill="#8a95a5" /><polygon :points="part.left" fill="#5d6878" /><polygon :points="part.right" fill="#6f7a8a" />
    </template>
    <polyline :points="[pt(11, 11.4, 0.55), pt(11, 14.2, 0.55)].join(' ')" stroke="#cbd5e1" stroke-width="2" />
    <polyline :points="[pt(12.4, 11.4, 0.55), pt(12.4, 14.2, 0.55)].join(' ')" stroke="#cbd5e1" stroke-width="2" />

    <!-- 재배치 경로 -->
    <polyline :points="route" fill="none" stroke="#fff" stroke-width="9" stroke-linecap="round" stroke-linejoin="round" opacity="0.35" />
    <polyline class="route" :points="route" fill="none" stroke="#34d399" stroke-width="4" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="2 9" />

    <!-- 관제 센터 -->
    <polygon :points="towerBox.top" :fill="tones.white[0]" />
    <polygon :points="towerBox.left" :fill="tones.white[1]" />
    <polygon :points="towerBox.right" :fill="tones.white[2]" />
    <polygon :points="roofBand" fill="#0f9d58" /><polygon :points="roofBandR" fill="#0b7a44" />
    <polygon :points="door" fill="#64748b" />
    <polygon :points="chartScreen" fill="#0b1220" />
    <polygon v-for="(b, i) in bars" :key="'b' + i" :points="b.pts" :fill="b.color" />
    <polygon :points="mapScreen" fill="#13213a" />
    <polyline :points="mapRoute" fill="none" stroke="#22c55e" stroke-width="1.6" stroke-dasharray="3 3" />
    <circle v-for="(m, i) in mapDots" :key="'m' + i" :cx="m.p[0]" :cy="m.p[1]" r="3.2" :fill="m.c" />
    <line :x1="antennaBase[0]" :y1="antennaBase[1]" :x2="antennaTop[0]" :y2="antennaTop[1]" stroke="#475569" stroke-width="2.5" />
    <circle :cx="antennaTop[0]" :cy="antennaTop[1]" r="4" fill="#0f9d58" />
    <circle class="signal" :cx="antennaTop[0]" :cy="antennaTop[1]" r="10" fill="none" stroke="#0f9d58" stroke-width="2" />
    <circle class="signal s2" :cx="antennaTop[0]" :cy="antennaTop[1]" r="10" fill="none" stroke="#0f9d58" stroke-width="2" />

    <!-- 건물·대여소·나무·트럭 (뒤에서 앞으로) -->
    <template v-for="(p, i) in sorted" :key="i">
      <g v-if="p.kind === 'building'">
        <polygon :points="box(p.x, p.y, 0, p.w, p.d, p.h).top" :fill="tones[p.tone][0]" />
        <polygon :points="box(p.x, p.y, 0, p.w, p.d, p.h).left" :fill="tones[p.tone][1]" />
        <polygon :points="box(p.x, p.y, 0, p.w, p.d, p.h).right" :fill="tones[p.tone][2]" />
        <polygon
          v-for="(win, j) in windows(p.x, p.y, p.w, p.d, p.h)"
          :key="j"
          :points="win"
          :fill="p.tone === 'white' ? '#a9bdd4' : '#ffffff'"
          :opacity="p.tone === 'white' ? 0.8 : 0.55"
        />
      </g>

      <g v-else-if="p.kind === 'station'">
        <template v-for="pad in [box(p.x, p.y, 0, STATION_W, STATION_D, 0.12)]" :key="'pad'">
          <polygon :points="pad.top" fill="#dde3ea" /><polygon :points="pad.left" fill="#b6c0cc" /><polygon :points="pad.right" fill="#c6cfd9" />
        </template>
        <!-- 거치대 -->
        <line v-for="(d, j) in docks(p)" :key="'k' + j" :x1="d[0]" :y1="d[1]" :x2="d[0]" :y2="d[1] - 7" stroke="#94a3b8" stroke-width="2" stroke-linecap="round" />
        <!-- 따릉이 키오스크 -->
        <template v-for="k in [box(p.x + STATION_W - 0.35, p.y + 0.2, 0.12, 0.28, 0.28, 0.95)]" :key="'kiosk'">
          <polygon :points="k.top" fill="#22b871" /><polygon :points="k.left" fill="#0f9d58" /><polygon :points="k.right" fill="#0b7a44" />
        </template>
        <g v-for="(b, j) in bikeSpots(p)" :key="'bk' + j" :transform="`translate(${b[0]} ${b[1]})`" class="bike">
          <circle cx="-4.5" cy="-3" r="3.2" /><circle cx="4.5" cy="-3" r="3.2" />
          <path d="M-4.5 -3 L-1 -9 L3 -9 L4.5 -3 M-1 -9 L0.8 -3 L-4.5 -3 M3 -9 L2.2 -11 M-2.2 -10.5 h2" />
        </g>
      </g>

      <g v-else-if="p.kind === 'tree'">
        <line :x1="xy(p.x, p.y)[0]" :y1="xy(p.x, p.y)[1]" :x2="xy(p.x, p.y)[0]" :y2="xy(p.x, p.y)[1] - 14" stroke="#8b6b4a" stroke-width="3" />
        <circle :cx="xy(p.x, p.y)[0]" :cy="xy(p.x, p.y)[1] - 22" :r="p.r * 26" fill="#4cb877" />
        <circle :cx="xy(p.x, p.y)[0] + 4" :cy="xy(p.x, p.y)[1] - 19" :r="p.r * 17" fill="#3aa364" />
      </g>

      <g v-else-if="p.kind === 'truck'" class="truck">
        <template v-for="t in [truckParts(p)]" :key="'t'">
          <ellipse v-for="(w, j) in t.wheels" :key="'wh' + j" :cx="w[0]" :cy="w[1]" rx="5.5" ry="4.5" fill="#1e293b" />
          <polygon :points="t.cargo.top" fill="#22b871" /><polygon :points="t.cargo.left" fill="#0f9d58" /><polygon :points="t.cargo.right" fill="#0b7a44" />
          <polygon :points="t.cab.top" fill="#ffffff" /><polygon :points="t.cab.left" fill="#e4e9f0" /><polygon :points="t.cab.right" fill="#c8d2df" />
          <polygon :points="t.windshield" fill="#1e3a5f" /><polygon :points="t.sideWindow" fill="#1e3a5f" />
          <g :transform="`translate(${t.logo[0]} ${t.logo[1] + 6}) scale(1.3)`" class="bike light">
            <circle cx="-4.5" cy="-3" r="3.2" /><circle cx="4.5" cy="-3" r="3.2" />
            <path d="M-4.5 -3 L-1 -9 L3 -9 L4.5 -3 M-1 -9 L0.8 -3 L-4.5 -3" />
          </g>
        </template>
      </g>
    </template>

    <!-- 대여소 상태 핀 -->
    <g v-for="(pin, i) in pins" :key="'pin' + i">
      <ellipse :cx="pin.sx" :cy="pin.sy" rx="10" ry="5" fill="#0f172a" opacity="0.12" />
      <g class="pin" :style="{ animationDelay: `${i * 0.4}s` }" :transform="`translate(${pin.x} ${pin.y})`">
        <path d="M0 0 C-6 -9 -15 -15 -15 -26 A15 15 0 1 1 15 -26 C15 -15 6 -9 0 0Z" :fill="pin.color" stroke="#fff" stroke-width="2.5" />
        <text x="0" y="-21.5" text-anchor="middle" font-size="13" font-weight="800" fill="#fff">{{ pin.text }}</text>
      </g>
    </g>

    <!-- AI 계획 → 사람 승인 카드 -->
    <line :x1="antennaTop[0]" :y1="antennaTop[1] - 6" x2="250" y2="104" stroke="#0f9d58" stroke-width="1.5" stroke-dasharray="3 4" />
    <g class="plan-card" filter="url(#cardShadow)">
      <rect x="236" y="20" width="196" height="88" rx="12" fill="#fff" />
      <circle cx="254" cy="40" r="5" fill="#0f9d58" />
      <text x="265" y="44" font-size="11" font-weight="700" fill="#0b7a44">AI 재배치 계획</text>
      <text x="252" y="66" font-size="13" font-weight="700" fill="#0f172a">과잉 → 부족 · 자전거 9대</text>
      <rect x="252" y="78" width="54" height="20" rx="6" fill="#0f9d58" />
      <text x="279" y="92" font-size="11" font-weight="700" fill="#fff" text-anchor="middle">승인</text>
      <rect x="312" y="78" width="54" height="20" rx="6" fill="#fff" stroke="#cbd5e1" />
      <text x="339" y="92" font-size="11" font-weight="600" fill="#334155" text-anchor="middle">수정</text>
    </g>
  </svg>
</template>

<style scoped>
.scene { display: block; width: 100%; height: 100%; font-family: inherit; }
.bike { fill: none; stroke: #0f9d58; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
.bike.light { stroke: #fff; }

.route { animation: flow 1.2s linear infinite; }
@keyframes flow { to { stroke-dashoffset: -22; } }

/* 트럭은 도로를 따라 +x 방향으로 지나간다 (격자 한 칸 = 화면 C, S 픽셀) */
.truck { animation: drive 9s linear infinite; }
@keyframes drive {
  0% { transform: translate(-78px, -45px); opacity: 0; }
  12% { opacity: 1; }
  88% { opacity: 1; }
  100% { transform: translate(26px, 15px); opacity: 0; }
}

.pin { animation: bob 2.4s ease-in-out infinite; transform-box: fill-box; }
@keyframes bob { 50% { translate: 0 -5px; } }

.plan-card { animation: bob 4s ease-in-out infinite; }

.signal { transform-box: fill-box; transform-origin: center; animation: ping 2.4s ease-out infinite; }
.signal.s2 { animation-delay: 1.2s; }
@keyframes ping { from { transform: scale(0.5); opacity: 0.9; } to { transform: scale(2.2); opacity: 0; } }

.cloud { animation: drift 18s ease-in-out infinite alternate; }
.cloud.c2 { animation-duration: 24s; }
.cloud.c3 { animation-duration: 30s; }
@keyframes drift { to { transform: translateX(-40px); } }

@media (prefers-reduced-motion: reduce) {
  .route, .truck, .pin, .plan-card, .signal, .cloud { animation: none; }
  .signal { opacity: 0; }
}
</style>
