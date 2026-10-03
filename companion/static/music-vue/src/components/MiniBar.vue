<script setup lang="ts">
// MiniBar.vue — P2.5+24(2026-10-03) → P3.3(2026-10-03)N7 长按收藏菜单
// 底部 88px mini bar:封面 56 + 曲名艺人 + ⏮⏯⏭ + 三层进度 + ♥ 收藏 + 🔊 音量。
// P3.3(2026-10-03):♥/♡ 按钮加 @contextmenu + touch 长按 600ms 触发 PopupMenu
//   4 项菜单:复制曲名+艺人 / 立即播放 / 查看所有收藏 (N) / 取消收藏 (仅已收藏显示)
//   复用 player.toggleFavorite() / playById() / listFavorites()
import { computed } from 'vue'
import { usePlayerStore } from '@/stores/player'
import { useUiStore } from '@/stores/ui'
import ProgressBar from './ProgressBar.vue'
import PopupMenu, { type MenuItem } from './PopupMenu.vue'
import { ref } from 'vue'

const player = usePlayerStore()
const ui = useUiStore()

const isPlaying = computed(() => player.status === 'playing')
const canFav = computed(() => !!player.currentTrack)

function fmt(sec: number): string {
  if (!sec || !isFinite(sec)) return '00:00'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

async function onToggle() {
  await player.toggle()
}
async function onNext() {
  await player.playNext()
}
async function onPrev() {
  await player.playPrev()
}
async function onFav() {
  const r = await player.toggleFavorite()
  if (r?.favorited != null) {
    ui.pushToast(r.favorited ? 'info' : 'warn',
                 r.favorited ? `已收藏: ${r.title || player.currentTrack?.title}` :
                               `已取消收藏: ${r.title || player.currentTrack?.title}`)
  }
}
// P2.5+25(2026-10-03):桌面歌词独立窗入口 — 仅壳内 IPC 有效,dev 模式优雅 toast 降级。
async function onOpenLyric() {
  const w = window as any
  if (typeof w?.prisIragent?.openLyric === 'function') {
    await w.prisIragent.openLyric()
  } else {
    ui.pushToast('warn', '请从托盘「🎤 桌面歌词」打开(壳层 IPC 未就绪)')
  }
}
function onVolume(e: Event) {
  const v = parseFloat((e.target as HTMLInputElement).value) / 100
  player.setVolume(v)
}

// ============================================================
// P3.3(2026-10-03)N7 长按收藏菜单 — ♡/♥ 按钮右键/touch 长按弹 PopupMenu
// ============================================================
const popupX = ref(0)
const popupY = ref(0)
const popupVisible = ref(false)
let longPressTimer: number | null = null
let longPressTriggered = false

const LONG_PRESS_MS = 600

function itemsForTrack(): MenuItem[] {
  return [
    { key: 'copy', icon: '📋', label: '复制曲名 + 艺人' },
    { key: 'play', icon: '▶', label: '立即播放' },
    { key: 'list', icon: '📂', label: '查看所有收藏' },
    // 「取消收藏」仅已收藏时显示
    { key: 'unfav', icon: '❌', label: '取消收藏', danger: true,
      hidden: !player.isFavorite },
  ]
}

async function openMenu(x: number, y: number) {
  popupX.value = x
  popupY.value = y
  popupVisible.value = true
}

function onFavContextMenu(e: MouseEvent) {
  // 桌面右键 → 弹菜单;不阻止默认 = 浏览器会同时弹原生菜单 → 必须 prevent
  e.preventDefault()
  if (!canFav.value) return
  void openMenu(e.clientX, e.clientY)
}

function onFavTouchStart(e: TouchEvent) {
  if (!canFav.value) return
  longPressTriggered = false
  const touch = e.touches[0]
  longPressTimer = window.setTimeout(() => {
    longPressTriggered = true
    if (touch) void openMenu(touch.clientX, touch.clientY)
  }, LONG_PRESS_MS)
}

function onFavTouchEnd(_e: TouchEvent) {
  if (longPressTimer != null) {
    clearTimeout(longPressTimer)
    longPressTimer = null
  }
  // 长按命中菜单 → 阻止后续 click 触发的 toggleFavorite
  if (longPressTriggered) {
    // 注:TouchEvent 没 preventDefault 这里已无意义,但加 marker 给 click 看
    longPressTriggered = false
  }
}

// click 处理:若刚长按 → 跳过 toggle
function onFavClick() {
  if (longPressTriggered) {
    longPressTriggered = false
    return
  }
  void onFav()
}

async function onSelectMenu(item: MenuItem) {
  popupVisible.value = false
  const tr = player.currentTrack
  if (!tr) return
  if (item.key === 'copy') {
    const text = `${tr.title} - ${tr.artist}`
    try {
      if (navigator?.clipboard?.writeText) {
        await navigator.clipboard.writeText(text)
        ui.pushToast('info', `已复制: ${text}`)
      } else {
        ui.pushToast('warn', '当前环境不支持剪贴板')
      }
    } catch (err) {
      ui.pushToast('warn', `复制失败: ${(err as Error)?.message || err}`)
    }
  } else if (item.key === 'play') {
    await player.playById(tr.id)
  } else if (item.key === 'list') {
    const r = await player.listFavorites()
    if (r.list.length === 0) {
      ui.pushToast('info', '暂无收藏')
    } else {
      ui.pushListToast('已收藏', r.list.map((f) => ({
        title: f.title,
        subtitle: f.artist,
      })))
    }
  } else if (item.key === 'unfav') {
    await onFav()
  }
}
</script>

<template>
  <div class="minibar">
    <!-- 左:封面 56 + 曲名艺人 -->
    <div class="left">
      <div class="cover-sm">{{ player.currentTrack?.title?.charAt(0) || '曲' }}</div>
      <div class="meta">
        <div class="title">{{ player.currentTrack?.title || '尚未选曲' }}</div>
        <div class="artist">{{ player.currentTrack?.artist || '' }}</div>
      </div>
    </div>

    <!-- 中:三件套 + 进度 + 时间 -->
    <div class="center">
      <div class="ctrls">
        <button class="ctrl" @click="onPrev" :disabled="!player.currentTrack" title="上一首">⏮</button>
        <button class="ctrl primary" @click="onToggle" :disabled="!player.currentTrack" :title="isPlaying ? '暂停' : '播放'">
          {{ isPlaying ? '⏸' : '▶' }}
        </button>
        <button class="ctrl" @click="onNext" :disabled="!player.currentTrack" title="下一首">⏭</button>
      </div>
      <div class="time-row">
        <span class="time">{{ fmt(player.currentTime) }}</span>
        <ProgressBar />
        <span class="time">{{ fmt(player.duration) }}</span>
      </div>
    </div>

    <!-- 右:🎤 桌面歌词 + ♥ 收藏 + 🔊 音量 -->
    <div class="right">
      <button class="ctrl lyric" @click="onOpenLyric" title="桌面歌词独立窗">🎤</button>
      <!-- P3.3(2026-10-03):♥/♡ 加 @contextmenu + touchstart/touchend 600ms 长按弹 PopupMenu -->
      <button class="ctrl fav" :class="{ active: player.isFavorite }"
              @click="onFavClick" @contextmenu="onFavContextMenu"
              @touchstart="onFavTouchStart" @touchend="onFavTouchEnd"
              :disabled="!canFav"
              :title="player.isFavorite ? '已收藏(右键菜单)' : '收藏(右键菜单)'">
        {{ player.isFavorite ? '♥' : '♡' }}
      </button>
      <div class="vol">
        <span class="vol-icon">🔊</span>
        <input type="range" min="0" max="100" :value="player.volume * 100"
               @input="onVolume" orient="vertical" />
      </div>
    </div>

    <!-- P3.3 长按收藏菜单 -->
    <PopupMenu :x="popupX" :y="popupY" :visible="popupVisible"
               :items="itemsForTrack()"
               @select="onSelectMenu"
               @close="popupVisible = false" />
  </div>
</template>

<style scoped>
.minibar {
  display: grid;
  grid-template-columns: 240px 1fr 200px;
  align-items: center;
  height: 100%;
  padding: 0 16px;
  gap: 16px;
}
.left {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.cover-sm {
  width: 56px; height: 56px;
  border-radius: 8px;
  background: linear-gradient(135deg, var(--gh-red), var(--gh-gold));
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  color: var(--gh-paper);
  font-weight: 700;
  flex-shrink: 0;
}
.meta { min-width: 0; flex: 1; }
.title {
  font-size: 13px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--gh-paper);
}
.artist {
  font-size: 11px;
  color: var(--gh-gray-light);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.center { display: flex; flex-direction: column; gap: 2px; }
.ctrls {
  display: flex;
  justify-content: center;
  gap: 18px;
  align-items: center;
}
.ctrl {
  font-size: 22px;
  color: var(--gh-paper);
  padding: 4px 8px;
}
.ctrl.primary {
  font-size: 30px;
  color: var(--gh-red-soft);
}
.time-row {
  display: grid;
  grid-template-columns: 48px 1fr 48px;
  align-items: center;
  gap: 8px;
}
.time {
  font-family: var(--font-mono);
  font-size: 11px;
  color: var(--gh-gray-light);
  text-align: center;
}
.right {
  display: flex;
  align-items: center;
  gap: 14px;
  justify-content: flex-end;
}
.ctrl.fav {
  font-size: 20px;
}
.ctrl.fav.active { color: var(--gh-red-soft); }
.vol {
  display: flex;
  align-items: center;
  gap: 6px;
}
.vol-icon { font-size: 14px; color: var(--gh-gray-light); }
input[type="range"] {
  width: 80px;
  accent-color: var(--gh-red);
}
</style>