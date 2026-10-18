import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // 开发模式：/api 代理到 Web 服务（8100），前端无需处理跨域
    proxy: { '/api': 'http://127.0.0.1:8100' }
  },
  build: { outDir: 'dist', emptyOutDir: true }
})
