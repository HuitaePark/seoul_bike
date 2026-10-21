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
  <div class="wrap">
    <aside class="hero">
      <span class="mark">🚲</span>
      <h1>따릉이 재배치 에이전트</h1>
      <p>계획은 에이전트가 세우고, 결정은 사람이 내린다.</p>
      <ul>
        <li><strong>관제 관리자</strong> 위험 거점 지도와 계획 승인</li>
        <li><strong>트럭 기사</strong> 오늘 경로와 현장 보고</li>
        <li><strong>운영사 책임자</strong> 도입 전후 성과 비교</li>
      </ul>
    </aside>
    <form class="card login" @submit.prevent="submit">
      <h1>로그인</h1>
      <label>아이디 <input v-model="loginId" autocomplete="username" required /></label>
      <label>비밀번호 <input v-model="password" type="password" autocomplete="current-password" required /></label>
      <p v-if="error" class="error">{{ error }}</p>
      <button :disabled="busy">{{ busy ? '확인 중…' : '로그인' }}</button>
      <RouterLink to="/signup" class="hint">계정이 없으면 회원가입</RouterLink>
      <p class="hint">시연 계정: manager · driver1 · driver2 · executive (비밀번호는 팀에 따로 공유)</p>
    </form>
  </div>
</template>

<style scoped>
.wrap { display: grid; grid-template-columns: 1fr 380px; gap: 40px; align-items: center; max-width: 900px; margin: 64px auto; }
.mark { display: grid; place-items: center; width: 56px; height: 56px; border-radius: 16px; background: var(--brand); font-size: 28px; margin-bottom: 18px; }
.hero h1 { font-size: 30px; }
.hero p { color: var(--ink-3); font-size: 16px; margin: 0 0 24px; }
.hero ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 10px; font-size: 14px; color: var(--ink-2); }
.hero li { padding: 12px 14px; background: #fff; border: 1px solid var(--line); border-radius: 12px; }
.hero li strong { color: var(--brand-strong); margin-right: 8px; }
.login { margin: 0; }
@media (max-width: 760px) {
  .wrap { grid-template-columns: minmax(0, 1fr); margin: 24px auto; }
}
</style>
