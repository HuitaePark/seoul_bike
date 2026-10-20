import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from './shared/auth/store'

// 화면 담당은 src/features/<기능>/ 아래에 화면을 만들고 여기에 경로를 추가한다.
export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', component: () => import('./features/login/LoginView.vue'), meta: { public: true } },
    { path: '/', component: () => import('./features/task-console/TaskConsoleView.vue') },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !useAuth().token) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
})
