<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../../shared/auth/store'
import AuthShell from './AuthShell.vue'

// "아이디 저장"은 이 브라우저에만 남는 편의 기능이라 저장이 막혀 있어도 로그인은 그대로 된다.
const SAVED_ID_KEY = 'login.savedId'
function readSavedId() {
  try {
    return localStorage.getItem(SAVED_ID_KEY) ?? ''
  } catch {
    return ''
  }
}
function writeSavedId(id: string) {
  try {
    if (id) localStorage.setItem(SAVED_ID_KEY, id)
    else localStorage.removeItem(SAVED_ID_KEY)
  } catch {
    /* 저장 불가 환경은 무시 */
  }
}

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const loginId = ref(readSavedId())
const remember = ref(loginId.value !== '')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await auth.login(loginId.value, password.value)
    writeSavedId(remember.value ? loginId.value : '')
    await router.replace(typeof route.query.redirect === 'string' ? route.query.redirect : '/')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <AuthShell>
    <form class="auth-form" @submit.prevent="submit">
      <label class="field">
        <span class="sr-only">아이디</span>
        <input v-model="loginId" placeholder="아이디" autocomplete="username" required />
      </label>
      <label class="field">
        <span class="sr-only">비밀번호</span>
        <input v-model="password" type="password" placeholder="비밀번호" autocomplete="current-password" required />
      </label>
      <label class="check"><input v-model="remember" type="checkbox" /> 아이디 저장</label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="submit" :disabled="busy">
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" aria-hidden="true">
          <path d="M12 3v8M6.3 6.8a8 8 0 1 0 11.4 0" />
        </svg>
        {{ busy ? '확인 중…' : '로그인' }}
      </button>
      <div class="links">
        <span>계정이 없으신가요?</span>
        <RouterLink to="/signup">회원가입 신청</RouterLink>
      </div>
      <p class="hint demo">시연 계정: manager · driver1 · driver2 · executive (비밀번호는 팀에 따로 공유)</p>
    </form>
  </AuthShell>
</template>
