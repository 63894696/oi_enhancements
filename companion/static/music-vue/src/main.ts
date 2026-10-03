// main.ts — P2.5+24(2026-10-03)Vue 3 + Pinia 入口
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import './styles/main.css'

const app = createApp(App)
app.use(createPinia())
app.mount('#app')