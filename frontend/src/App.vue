<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from './shared/auth/store'
import { roleLabel } from './shared/labels'

const auth = useAuth()
const route = useRoute()
// 기사 앱은 모바일 전용 틀을 직접 그린다 (상단 바 없음).
const mobile = computed(() => route.meta.layout === 'mobile')
const initial = computed(() => auth.user?.name.slice(0, 1) ?? '')
</script>

<template>
  <RouterView v-if="mobile" />
  <template v-else>
    <header class="topbar">
      <RouterLink to="/" class="brand">
        <span class="brand-mark" aria-hidden="true">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5.5" cy="17" r="3.5" /><circle cx="18.5" cy="17" r="3.5" /><path d="M5.5 17 9 9h6l3.5 8M9 9 12 17h1.5M14 5h2.5" />
          </svg>
        </span>
        따릉이 재배치 <small>Rebalancing Agent</small>
      </RouterLink>
      <nav v-if="auth.user">
        <template v-if="auth.user.role === 'MANAGER'">
          <RouterLink to="/dashboard">관제 대시보드</RouterLink>
          <RouterLink to="/console">작업 콘솔</RouterLink>
          <RouterLink to="/plans/review">계획 검토</RouterLink>
          <RouterLink to="/admin/users">계정 관리</RouterLink>
          <a href="/swagger" target="_blank">API 계약</a>
        </template>
        <template v-else-if="auth.user.role === 'EXECUTIVE'">
          <RouterLink to="/report">성과 리포트</RouterLink>
          <RouterLink to="/console">질의</RouterLink>
          <RouterLink to="/plans/review">계획 검토</RouterLink>
        </template>
        <a href="/status" target="_blank">시스템 상태</a>
        <span class="who">
          <span class="avatar">{{ initial }}</span>
          {{ auth.user.name }} · {{ roleLabel[auth.user.role] }}
          <button class="ghost" @click="auth.logout()">로그아웃</button>
        </span>
      </nav>
    </header>
    <main><RouterView /></main>
  </template>
</template>
