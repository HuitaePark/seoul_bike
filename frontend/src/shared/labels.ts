import type { Schemas } from './api/client'

/** 역할 표시 이름. 상단 메뉴, 회원가입, 계정 관리가 함께 쓴다. */
export const roleLabel: Record<Schemas['Role'], string> = {
  MANAGER: '관제 관리자',
  DRIVER: '트럭 기사',
  EXECUTIVE: '운영사 책임자',
}

/** 회사명. 아직 정하지 않아 자리만 잡아 둔다 (로그인·회원가입 화면이 쓴다). */
export const companyName = '회사명'
