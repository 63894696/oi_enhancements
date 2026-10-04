// vite.config.ts — P2.5+24(2026-10-03)music-vue 工程配置
// base='/music-vue/' 因为 music 后端把 vite dist 挂在 /music-vue/ 路径下。
// dev proxy 把 /api + /ws 转发到 music 后端(默认 8662 端口或环境变量覆盖)。
//
// P2.5+25(2026-10-03):多入口构建 — main(music 子窗) + lyric(桌面歌词独立窗)。
// vite 自动拆 chunk:vue/pinia/emitter 公共 chunk + 2 个独立 page chunk。
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
    rollupOptions: {
      input: {
        // P2.5+25:多入口 — main 走 index.html;桌面歌词独立窗走 lyric.html
        main: fileURLToPath(new URL('./index.html', import.meta.url)),
        lyric: fileURLToPath(new URL('./lyric.html', import.meta.url)),
        // P3.5(2026-10-04):桌面 EQ 独立窗走 eq.html(hash 路由 #/eq-window)
        eq: fileURLToPath(new URL('./eq.html', import.meta.url)),
      },
    },
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