// 구역(regionId) 목록과 근무조 ID 규칙. 계획 검토·작업 콘솔 등 구역이나 근무조를 고르는 화면이 같이 쓴다.
// 구역 목록 API는 아직 계약에 없어 화면에 둔다. 서울 활동 구역 17개(N01~N08 강북, S01~S09 강남)다
// (analysis/out/ref_regions.csv, docs/data/agreements.md 2·25번). 관제 대시보드는 시연 구역 N02(광진·성동)로 고정이다.
// 고르는 칸은 이 목록에 있는 구역만 값으로 받는다 (검색어가 그대로 regionId가 되어 `광진:날짜:AM` 같은 근무조 ID가 만들어졌다).

export type Region = { id: string; name: string; note?: string }

export const REGIONS: Region[] = [
  ...['01', '02', '03', '04', '05', '06', '07', '08'].map((n) => ({ id: `N${n}`, name: `강북-${n}`, note: n === '02' ? '시연 구역 (광진·성동)' : undefined })),
  ...['01', '02', '03', '04', '05', '06', '07', '08', '09'].map((n) => ({ id: `S${n}`, name: `강남-${n}` })),
]

export const regionOf = (id: string) => REGIONS.find((r) => r.id === id)

/** 이름·ID·설명에 검색어가 들어간 구역 (대소문자 무시). 검색어가 없으면 전부. "광진"으로 N02를 찾을 수 있다. */
export function searchRegions(query: string): Region[] {
  const q = query.trim().toLocaleLowerCase()
  return q ? REGIONS.filter((r) => `${r.id} ${r.name} ${r.note ?? ''}`.toLocaleLowerCase().includes(q)) : REGIONS
}

/** 입력한 글자가 권역 ID나 이름과 정확히 같으면 그 권역 (앞뒤 공백·대소문자 무시). 검색 중인 일부 글자는 맞추지 않는다. */
export function exactRegion(text: string): Region | undefined {
  const q = text.trim().toLocaleLowerCase()
  return REGIONS.find((r) => r.id.toLocaleLowerCase() === q || r.name.toLocaleLowerCase() === q)
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

/** 지금(서울 시각) 하고 있거나 다음에 올 시간대. 오후 근무 시작(13시) 전은 오전, 저녁 근무 시작(19시) 전은 오후. */
export function slotAt(now = new Date()): ShiftSlot {
  const hour = Number(new Intl.DateTimeFormat('en-US', { timeZone: 'Asia/Seoul', hour: 'numeric', hourCycle: 'h23' }).format(now))
  return hour < 13 ? 'AM' : hour < 19 ? 'PM' : 'EVE'
}
