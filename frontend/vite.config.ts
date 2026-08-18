import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    host: '0.0.0.0',
    allowedHosts: true,
    proxy: {
      '/chat': 'http://127.0.0.1:8001',
      '/health': 'http://127.0.0.1:8001',
      '/sessions': 'http://127.0.0.1:8001',
      '/feedback': 'http://127.0.0.1:8001',
      '/admin/api': 'http://127.0.0.1:8001',
      '/api': 'http://127.0.0.1:8001',
    },
  },
})
