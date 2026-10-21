import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { api, bindAuth, problemMessage, type Schemas } from '../api/client'

const STORAGE_KEY = 'seoul-bike.auth'

type Saved = { token: string; user: Schemas['User']; expiresAt: number }

function load(): Saved | null {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? 'null') as Saved | null
    return saved && saved.expiresAt > Date.now() ? saved : null
  } catch {
    return null
  }
}

/**
 * 로그인 상태. 권한 판단은 backend가 한다 (과제 금지 사항). 화면은 role로 보여줄 것만 고른다.
 */
export const useAuth = defineStore('auth', () => {
  const saved = ref<Saved | null>(load())
  const token = computed(() => saved.value?.token ?? null)
  const user = computed(() => saved.value?.user ?? null)

  function keep(data: Schemas['LoginResponse']) {
    saved.value = { token: data.accessToken, user: data.user, expiresAt: Date.now() + data.expiresIn * 1000 }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved.value))
  }

  async function login(loginId: string, password: string) {
    const { data, error } = await api.POST('/api/auth/login', { body: { loginId, password } })
    if (error || !data) throw new Error(problemMessage(error))
    keep(data)
  }

  /** 로그인 없이 들어오기: 손님 계정(관리자 권한) 토큰을 받는다 */
  async function loginAsGuest() {
    const { data, error } = await api.POST('/api/auth/guest')
    if (error || !data) throw new Error(problemMessage(error))
    keep(data)
  }

  function logout() {
    saved.value = null
    localStorage.removeItem(STORAGE_KEY)
  }

  bindAuth(() => token.value, logout)

  return { token, user, login, loginAsGuest, logout }
})
