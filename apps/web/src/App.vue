<script setup lang="ts">
import { useAuth } from './shared/auth/store'

const auth = useAuth()
const roleLabel = { MANAGER: '관제 관리자', DRIVER: '트럭 기사', EXECUTIVE: '운영사 책임자' } as const
</script>

<template>
  <header class="topbar">
    <strong>따릉이 재배치 에이전트</strong>
    <nav v-if="auth.user">
      <span>{{ auth.user.name }} · {{ roleLabel[auth.user.role] }}</span>
      <a href="/swagger" target="_blank">API 계약</a>
      <a href="/status" target="_blank">시스템 상태</a>
      <button class="ghost" @click="auth.logout()">로그아웃</button>
    </nav>
  </header>
  <main><RouterView /></main>
</template>
