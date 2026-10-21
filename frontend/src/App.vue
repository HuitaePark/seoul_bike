<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useAuth } from './shared/auth/store'
import { companyName, roleLabel } from './shared/labels'

const auth = useAuth()
const route = useRoute()
// 기사 앱과 로그인·회원가입은 틀을 직접 그린다 (상단 바 없음).
const bare = computed(() => route.meta.layout !== undefined)
const initial = computed(() => auth.user?.name.slice(0, 1) ?? '')

// 상단 오른쪽은 프로필 사진(이니셜)만 둔다. 누르면 이름·역할과 프로필·계정·로그아웃 메뉴가 아래로 열린다.
const menuOpen = ref(false)
const who = ref<HTMLElement | null>(null)
function closeOutside(e: MouseEvent) {
  if (menuOpen.value && !who.value?.contains(e.target as Node)) menuOpen.value = false
}
onMounted(() => document.addEventListener('click', closeOutside))
onBeforeUnmount(() => document.removeEventListener('click', closeOutside))
watch(() => route.fullPath, () => (menuOpen.value = false))
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
          <!-- 계획 만들기·현장 상황 반영 요청은 여기서 한다. 설명은 마우스를 올리면 뜬다 -->
          <RouterLink to="/console" class="has-tip" data-tip="계획 만들기 · 현장 상황 반영">작업 콘솔<span class="sr-only"> (계획 만들기 · 현장 상황 반영)</span></RouterLink>
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
        <span ref="who" class="who" @keydown.esc="menuOpen = false">
          <button
            class="avatar-btn"
            aria-haspopup="menu"
            :aria-expanded="menuOpen"
            :aria-label="`${auth.user.name} 계정 메뉴`"
            :title="`${auth.user.name} · ${roleLabel[auth.user.role]}`"
            @click="menuOpen = !menuOpen"
          >
            <span class="avatar">{{ initial }}</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6" /></svg>
          </button>
          <div v-if="menuOpen" class="who-menu" role="menu">
            <div class="who-head">
              <span class="avatar big">{{ initial }}</span>
              <span><strong>{{ auth.user.name }}</strong><small>{{ roleLabel[auth.user.role] }}<template v-if="auth.user.truckId"> · {{ auth.user.truckId }}</template></small></span>
            </div>
            <RouterLink to="/me" role="menuitem">내 프로필</RouterLink>
            <RouterLink v-if="auth.user.role === 'MANAGER'" to="/admin/users" role="menuitem">계정 설정 (계정 관리)</RouterLink>
            <button role="menuitem" class="logout" @click="auth.logout()">로그아웃</button>
          </div>
        </span>
      </nav>
    </header>
    <main :class="{ wide: route.meta.wide }"><RouterView /></main>
  </template>
</template>
