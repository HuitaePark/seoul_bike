<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from './shared/auth/store'
import { companyName, roleLabel } from './shared/labels'

const auth = useAuth()
const route = useRoute()
// 기사 앱과 로그인·회원가입은 틀을 직접 그린다 (상단 바 없음).
const bare = computed(() => route.meta.layout !== undefined)
const initial = computed(() => auth.user?.name.slice(0, 1) ?? '')
</script>

<template>
  <RouterView v-if="bare" />
  <template v-else>
    <header class="topbar">
      <RouterLink to="/" class="brand">
        <span class="brand-mark" aria-hidden="true">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="5.5" cy="17" r="3.5" /><circle cx="18.5" cy="17" r="3.5" /><path d="M5.5 17 9 9h6l3.5 8M9 9 12 17h1.5M14 5h2.5" />
          </svg>
        </span>
        <span class="brand-text">{{ companyName }} <small>따릉이 재배치 에이전트</small></span>
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
          <span class="who-name">{{ auth.user.name }} · {{ roleLabel[auth.user.role] }}</span>
          <button class="ghost" @click="auth.logout()">로그아웃</button>
        </span>
      </nav>
    </header>
    <main :class="{ wide: route.meta.wide }"><RouterView /></main>
  </template>
</template>
