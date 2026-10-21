<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { useAuth } from '../../shared/auth/store'
import { roleLabel } from '../../shared/labels'

// 내 프로필 (상단 프로필 메뉴). GET /api/me로 서버가 아는 내 정보를 보여 준다.
// 이름·비밀번호를 바꾸는 API는 아직 계약에 없어 조회만 한다. 바꿀 일이 있으면 관리자가 계정 관리에서 처리한다.

const auth = useAuth()
const me = ref<Schemas['User'] | null>(auth.user)
const error = ref('')

onMounted(async () => {
  const { data, error: e } = await api.GET('/api/me')
  if (e) error.value = problemMessage(e)
  else if (data) me.value = data
})
</script>

<template>
  <section v-if="me" class="card profile">
    <div class="head">
      <span class="avatar big" aria-hidden="true">{{ me.name.slice(0, 1) }}</span>
      <div>
        <h2>{{ me.name }}</h2>
        <span class="muted">{{ roleLabel[me.role] }}</span>
      </div>
    </div>
    <dl>
      <div><dt>이름</dt><dd>{{ me.name }}</dd></div>
      <div><dt>역할</dt><dd>{{ roleLabel[me.role] }}</dd></div>
      <div v-if="me.truckId"><dt>트럭</dt><dd>{{ me.truckId }}</dd></div>
      <div><dt>계정 ID</dt><dd class="mono">{{ me.id }}</dd></div>
    </dl>
    <p class="muted">이름·비밀번호 변경은 아직 화면에서 할 수 없다. 관리자에게 요청한다.</p>
    <p v-if="error" class="error">최신 정보를 불러오지 못했다: {{ error }}</p>
    <button class="ghost" @click="auth.logout()">로그아웃</button>
  </section>
</template>

<style scoped>
.profile { max-width: 520px; display: flex; flex-direction: column; gap: 14px; }
.head { display: flex; align-items: center; gap: 12px; }
.head h2 { margin: 0; }
dl { display: grid; gap: 8px; margin: 0; }
dl div { display: grid; grid-template-columns: 90px 1fr; padding: 10px 12px; background: #f8fafc; border-radius: 10px; font-size: 14px; }
dt { color: var(--ink-3); font-weight: 600; }
dd { margin: 0; font-weight: 600; overflow-wrap: anywhere; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; }
.profile p { margin: 0; }
.profile button { align-self: flex-start; }
</style>
