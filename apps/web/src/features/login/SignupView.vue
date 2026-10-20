<script setup lang="ts">
import { ref } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'

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
  <div class="card login">
    <h1>회원가입</h1>
    <template v-if="done">
      <p>가입 신청을 받았다. <strong>관리자가 승인하면</strong> 로그인할 수 있다.</p>
      <RouterLink to="/login">로그인 화면으로</RouterLink>
    </template>
    <form v-else @submit.prevent="submit">
      <label>
        아이디
        <input v-model="form.loginId" autocomplete="username" pattern="[a-z0-9._-]{4,32}" required />
        <span class="hint">영문 소문자·숫자·. _ - 4~32자</span>
      </label>
      <label>
        비밀번호
        <input v-model="form.password" type="password" autocomplete="new-password" minlength="8" maxlength="72" required />
        <span class="hint">8자 이상</span>
      </label>
      <label>이름 <input v-model="form.name" maxlength="50" required /></label>
      <label>
        희망 역할
        <select v-model="form.requestedRole">
          <option value="DRIVER">트럭 기사</option>
          <option value="MANAGER">관제 관리자</option>
          <option value="EXECUTIVE">운영사 책임자</option>
        </select>
        <span class="hint">관리자가 승인할 때 역할과 트럭을 확정한다</span>
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button :disabled="busy">가입 신청</button>
      <RouterLink to="/login" class="hint">이미 계정이 있다</RouterLink>
    </form>
  </div>
</template>
