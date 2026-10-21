import { createRouter, createWebHistory } from 'vue-router'
import type { Schemas } from './shared/api/client'
import { useAuth } from './shared/auth/store'

declare module 'vue-router' {
  interface RouteMeta {
    /** 로그인 없이 연다 */
    public?: boolean
    /** 이 역할만 연다. 화면을 고르는 용도이고, 권한은 backend가 다시 막는다. */
    roles?: Schemas['Role'][]
    /** 상단 바 없이 화면이 틀을 직접 그린다. mobile은 기사 앱, full은 로그인·회원가입 */
    layout?: 'mobile' | 'full'
    /** 본문 폭을 넓게 쓴다 (관제 대시보드) */
    wide?: boolean
  }
}

/** 역할별 첫 화면. 관리자는 관제 대시보드, 기사는 기사 앱, 책임자는 성과 리포트. */
const home: Record<Schemas['Role'], string> = {
  MANAGER: '/dashboard',
  DRIVER: '/driver',
  EXECUTIVE: '/report',
}

// 화면 담당은 src/features/<기능>/ 아래에 화면을 만들고 여기에 경로를 추가한다.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./features/login/LoginView.vue'), meta: { public: true, layout: 'full' } },
    { path: '/signup', component: () => import('./features/login/SignupView.vue'), meta: { public: true, layout: 'full' } },
    {
      path: '/dashboard',
      component: () => import('./features/dashboard/DashboardView.vue'),
      meta: { roles: ['MANAGER'], wide: true },
    },
    {
      path: '/driver',
      component: () => import('./features/driver/DriverView.vue'),
      meta: { roles: ['DRIVER'], layout: 'mobile' },
    },
    {
      path: '/report',
      component: () => import('./features/report/ReportView.vue'),
      meta: { roles: ['EXECUTIVE', 'MANAGER'] },
    },
    {
      path: '/admin/users',
      component: () => import('./features/admin-users/AdminUsersView.vue'),
      meta: { roles: ['MANAGER'] },
    },
    {
      // 에이전트 추론 로그: 모델이 도구를 고른 이유·결과 (작업 콘솔과 별개, backend /api/admin/** 가 MANAGER만 연다)
      path: '/admin/agent-log',
      component: () => import('./features/agent-log/AgentLogView.vue'),
      meta: { roles: ['MANAGER'], wide: true },
    },
    { path: '/plans/review/demo', component: () => import('./features/plan-map/PlanReviewView.vue'), props: { demo: true }, meta: { public: true } },
    { path: '/plans/review', component: () => import('./features/plan-map/PlanReviewView.vue'), meta: { roles: ['MANAGER', 'EXECUTIVE'] } },
    { path: '/console', component: () => import('./features/task-console/TaskConsoleView.vue') },
    { path: '/me', component: () => import('./features/profile/ProfileView.vue') },
    {
      path: '/',
      redirect: () => {
        const role = useAuth().user?.role
        return role ? home[role] : '/login'
      },
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuth()
  if (!to.meta.public && !auth.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && auth.user && !to.meta.roles.includes(auth.user.role)) {
    return { path: home[auth.user.role] }
  }
})
