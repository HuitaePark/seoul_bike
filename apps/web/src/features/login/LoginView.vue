<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuth } from '../../shared/auth/store'

const auth = useAuth()
const route = useRoute()
const router = useRouter()
const loginId = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  try {
    await auth.login(loginId.value, password.value)
    await router.replace(typeof route.query.redirect === 'string' ? route.query.redirect : '/')
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <form class="card login" @submit.prevent="submit">
    <h1>로그인</h1>
    <label>아이디 <input v-model="loginId" autocomplete="username" required /></label>
    <label>비밀번호 <input v-model="password" type="password" autocomplete="current-password" required /></label>
    <p v-if="error" class="error">{{ error }}</p>
    <button :disabled="busy">{{ busy ? '확인 중…' : '로그인' }}</button>
    <RouterLink to="/signup" class="hint">계정이 없으면 회원가입</RouterLink>
    <p class="hint">시연 계정: manager · driver1 · driver2 · executive (비밀번호는 팀에 따로 공유)</p>
  </form>
</template>
