// 권역(regionId) 목록과 근무조 ID 규칙. 계획 검토·작업 콘솔 등 권역이나 근무조를 고르는 화면이 같이 쓴다.
// 권역 목록 API는 아직 계약에 없어 화면에 둔다. 지금 시스템(가짜 MCP·더미 데이터)이 아는 권역은 gwangjin-seongdong 하나이고,
// N01~S09는 데이터 담당이 낸 활동 구역 17개다 (analysis/out/ref_regions.csv, docs/data/agreements.md 2번: 전원 합의 뒤 적용).
// 목록에 없는 ID도 직접 입력할 수 있다.

export type Region = { id: string; name: string; note?: string }

export const REGIONS: Region[] = [
  { id: 'gwangjin-seongdong', name: '광진·성동', note: '시연 권역' },
  ...['01', '02', '03', '04', '05', '06', '07', '08'].map((n) => ({ id: `N${n}`, name: `강북-${n}`, note: '활동 구역 제안' })),
  ...['01', '02', '03', '04', '05', '06', '07', '08', '09'].map((n) => ({ id: `S${n}`, name: `강남-${n}`, note: '활동 구역 제안' })),
]

export const regionOf = (id: string) => REGIONS.find((r) => r.id === id)

/** 이름이나 ID에 검색어가 들어간 권역 (대소문자 무시). 검색어가 없으면 전부. */
export function searchRegions(query: string): Region[] {
  const q = query.trim().toLocaleLowerCase()
  return q ? REGIONS.filter((r) => `${r.id} ${r.name}`.toLocaleLowerCase().includes(q)) : REGIONS
}

/** 근무 시간대. docs/data/agreements.md 3번 (AM 07–10, PM 13–16, EVE 19–21) */
export const SHIFT_SLOTS = [
  { id: 'AM', label: '오전', time: '07:00–10:00' },
  { id: 'PM', label: '오후', time: '13:00–16:00' },
  { id: 'EVE', label: '저녁', time: '19:00–21:00' },
] as const
export type ShiftSlot = (typeof SHIFT_SLOTS)[number]['id']

/** 근무조 ID `{regionId}:{yyyy-MM-dd}:{AM|PM|EVE}` */
export const makeShiftId = (regionId: string, date: string, slot: ShiftSlot) => `${regionId.trim()}:${date}:${slot}`

/**
 * 오늘 날짜(서울 시각, yyyy-MM-dd). toISOString()은 UTC라 서울 오전 9시 전에는 어제가 된다
 * (오전 근무조 07:00–10:00에 어제 근무조 ID가 만들어진다).
 */
export function todayInSeoul(now = new Date()): string {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Seoul' }).format(now)
}
