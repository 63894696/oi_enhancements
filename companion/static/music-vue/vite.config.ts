// vite.config.ts — P2.5+24(2026-10-03)music-vue 工程配置
// base='/music-vue/' 因为 music 后端把 vite dist 挂在 /music-vue/ 路径下。
// dev proxy 把 /api + /ws 转发到 music 后端(默认 8662 端口或环境变量覆盖)。
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

const BACKEND = process.env.VITE_MUSIC_BACKEND || 'http://127.0.0.1:8662'

export default defineConfig({
  plugins: [vue()],
  base: '/music-vue/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2022',
    minify: 'esbuild',
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: BACKEND,
        changeOrigin: true,
      },
      '/ws': {
        target: BACKEND.replace(/^http/, 'ws'),
        ws: true,
      },
    },
  },
})