import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// 개발 서버는 backend(:8080)로 프록시한다. 배포에서는 nginx가 같은 일을 한다.
// 화면과 API가 같은 출처라서 CORS 설정이 필요 없다.
const backend = process.env.BACKEND_URL ?? 'http://localhost:8080'

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': backend,
      '/swagger': backend,
      '/specs': backend,
      '/webjars': backend,
      '/actuator': backend,
    },
  },
})
