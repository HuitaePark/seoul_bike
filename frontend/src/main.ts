import { createPinia } from 'pinia'
import { createApp, watch } from 'vue'
import App from './App.vue'
import { router } from './router'
import { useAuth } from './shared/auth/store'
import './shared/styles.css'

const app = createApp(App).use(createPinia()).use(router)

// 토큰이 만료되거나 401을 받아 로그아웃되면 로그인 화면으로 보낸다.
const auth = useAuth()
watch(
  () => auth.token,
  (token) => {
    if (!token && router.currentRoute.value.path !== '/login') router.push('/login')
  },
)

app.mount('#app')
