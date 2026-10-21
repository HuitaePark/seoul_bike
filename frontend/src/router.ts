import { createRouter, createWebHistory } from 'vue-router'
import type { Schemas } from './shared/api/client'
import { useAuth } from './shared/auth/store'

declare module 'vue-router' {
  interface RouteMeta {
    /** 로그인 없이 연다 */
    public?: boolean
    /** 이 역할만 연다. 화면을 고르는 용도이고, 권한은 backend가 다시 막는다. */
    roles?: Schemas['Role'][]
  }
}

// 화면 담당은 src/features/<기능>/ 아래에 화면을 만들고 여기에 경로를 추가한다.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./features/login/LoginView.vue'), meta: { public: true } },
    { path: '/signup', component: () => import('./features/login/SignupView.vue'), meta: { public: true } },
    {
      path: '/admin/users',
      component: () => import('./features/admin-users/AdminUsersView.vue'),
      meta: { roles: ['MANAGER'] },
    },
    { path: '/', component: () => import('./features/task-console/TaskConsoleView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  const auth = useAuth()
  if (!to.meta.public && !auth.token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (to.meta.roles && auth.user && !to.meta.roles.includes(auth.user.role)) {
    return { path: '/' }
  }
})
