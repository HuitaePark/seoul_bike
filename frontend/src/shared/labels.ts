import type { Schemas } from './api/client'

/** 역할 표시 이름. 상단 메뉴, 회원가입, 계정 관리가 함께 쓴다. */
export const roleLabel: Record<Schemas['Role'], string> = {
  MANAGER: '관제 관리자',
  DRIVER: '트럭 기사',
  EXECUTIVE: '운영사 책임자',
}

/** 회사명 (2026-10-21 확정). 상단 바와 로그인·회원가입 화면이 쓴다. */
export const companyName = '한트럭'
