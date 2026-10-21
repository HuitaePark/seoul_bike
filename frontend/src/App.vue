<script setup lang="ts">
import { useAuth } from './shared/auth/store'
import { roleLabel } from './shared/labels'

const auth = useAuth()
</script>

<template>
  <header class="topbar">
    <strong>따릉이 재배치 에이전트</strong>
    <nav v-if="auth.user">
      <RouterLink to="/">작업 콘솔</RouterLink>
      <RouterLink v-if="auth.user.role !== 'DRIVER'" to="/plans/review">계획 검토</RouterLink>
      <RouterLink v-if="auth.user.role === 'MANAGER'" to="/admin/users">계정 관리</RouterLink>
      <a href="/swagger" target="_blank">API 계약</a>
      <a href="/status" target="_blank">시스템 상태</a>
      <span>{{ auth.user.name }} · {{ roleLabel[auth.user.role] }}</span>
      <button class="ghost" @click="auth.logout()">로그아웃</button>
    </nav>
  </header>
  <main><RouterView /></main>
</template>

<style scoped>
.topbar { flex-wrap: wrap; gap: 12px; }
.topbar nav { flex-wrap: wrap; }
@media (max-width: 600px) { .topbar { padding: 16px 20px; } .topbar nav { gap: 10px 14px; } }
</style>
