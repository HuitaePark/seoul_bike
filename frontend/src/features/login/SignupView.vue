<script setup lang="ts">
import { ref } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { roleLabel } from '../../shared/labels'
import AuthShell from './AuthShell.vue'

const form = ref({ loginId: '', password: '', name: '', requestedRole: 'DRIVER' as Schemas['Role'] })
const error = ref('')
const done = ref(false)
const busy = ref(false)

async function submit() {
  busy.value = true
  error.value = ''
  const { error: e } = await api.POST('/api/auth/signup', { body: form.value })
  busy.value = false
  if (e) error.value = problemMessage(e)
  else done.value = true
}
</script>

<template>
  <AuthShell>
    <h1 class="auth-title">회원가입 신청</h1>
    <div v-if="done" class="auth-form">
      <p>가입 신청을 받았다. <strong>관리자가 승인하면</strong> 로그인할 수 있다.</p>
      <RouterLink to="/login" class="submit as-link">로그인 화면으로</RouterLink>
    </div>
    <form v-else class="auth-form" @submit.prevent="submit">
      <label class="field">
        아이디
        <input v-model="form.loginId" autocomplete="username" pattern="[a-z0-9._-]{4,32}" required />
        <span class="hint">영문 소문자·숫자·. _ - 4~32자</span>
      </label>
      <label class="field">
        비밀번호
        <input v-model="form.password" type="password" autocomplete="new-password" minlength="8" maxlength="72" required />
        <span class="hint">8자 이상</span>
      </label>
      <label class="field">이름 <input v-model="form.name" maxlength="50" required /></label>
      <label class="field">
        희망 역할
        <select v-model="form.requestedRole">
          <option v-for="(label, role) in roleLabel" :key="role" :value="role">{{ label }}</option>
        </select>
        <span class="hint">관리자가 승인할 때 역할과 트럭을 확정한다</span>
      </label>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
      <button class="submit" :disabled="busy">가입 신청</button>
      <div class="links">
        <span>이미 계정이 있으신가요?</span>
        <RouterLink to="/login">로그인</RouterLink>
      </div>
    </form>
  </AuthShell>
</template>
