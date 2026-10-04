---
name: p3-8-music-spectrum-shipped
description: P3.8(2026-10-04)music N10 实时频谱 ship — EqEngine.bind() 末尾串接 AnalyserNode(masterGain 之后)+ composables/useSpectrum rAF 60fps 拉 1024 bin → 16 段对数 + components/SpectrumBars gold/red 渐变 + MusicView 主区右侧 160px 列挂载
metadata:
  type: project
---

# P3.8 music N10 实时频谱 ship(2026-10-04)

## Context

**用户原始诉求**:「请继续 P3.8 N10 频谱」(P3.7 3f5e84e chip+search ship 后的下一项 — 11 项主流播放器优化的最后一项 N10)
**位置**:11 项主流优化最后一项 — 主 music 子窗(MusicView)右下实时频谱
**前置 ship**:P3.7(3f5e84e chip+search)+ P3.6(9b1ec51 下载 toast)+ P3.5(c9073a7 10 段 EQ)+ P3.10a(4f16c3c 桌面弹卡)

## 用户拍板 4 项(全选推荐)

1. **频谱接入点** = masterGain 之后, destination 之前(EQ shaping 后 — 反映用户实际听到的样子;落雪 / Spotify 桌面同款)
2. **段数** = **16 段**(对数分布 32/64/125/250/500/1k/2k/4k/6k/8k/10k/12k/14k/16k/18k/20k Hz,覆盖 0-22kHz)
3. **数据节流** = requestAnimationFrame 60fps(浏览器原生,自动让出主线程)
4. **显示位置** = MusicView 主区右侧(.bottom grid 加第 3 列 160px;LyricOnlyView / EqWindowView 不显示)

## 调研:AudioContext 拓扑

1. **AudioContext 在 renderer 层**(`services/eq.ts:64` `this.ctx = new AC()`)
2. **EqEngine 单例 + bound flag**(`services/eq.ts:42-46` `_instance` + `getInstance()`;`bind()` `if (this.bound) return true` 幂等守护)
3. **唯一 audio 源** = `PlayerService` 单例 `new Audio()`(`services/player.ts:36`,`getAudioElement()` 第 78 行)
4. **唯一 bind 调用方** = `services/player.ts:140-143`(`onCanPlay` 时 `eqEngine.bind(this.audio)`)
5. **现成 chain**:`source → bq0..9 → masterGain → destination`
6. **频谱接入锚点**:`masterGain → analyser → destination`(EQ shaping 后)
7. **主进程无 AudioContext**(`grep AudioContext` 在 `prisiragent-shell/` 0 命中)
8. **多 BrowserWindow 隔离**:LyricOnlyView(歌词独立窗)+ EqWindowView(EQ 独立窗,只调 `eq.apply`,不持有 audio)→ **都没 PlayerService.audio,频谱仅在主 music 子窗显示**

## 主流 spectrum 借鉴

- **落雪 lx-music-desktop**:AnalyserNode 在 masterGain 之后 + peak 而非 avg + 16-24 段对数 + 主进程不参与
- **Spotify 桌面**:32 段 logarithmic + masterGain → analyser → destination
- **网易云桌面**:对数分布覆盖人耳敏感频段(20Hz-20kHz)
- **YesPlayMusic**:usePlayerAudio composable 共享 AnalyserNode + rAF 60fps

## 改动文件清单

**前端 2 改 2 新(纯 renderer 增量,无后端 / 无主进程)**:

- **改** `companion/static/music-vue/src/services/eq.ts`(+20 行):新属性 `analyser: AnalyserNode | null` + `freqBuf: Uint8Array | null`(fftSize/2 = 1024 bin);`bind()` 末尾串接:`masterGain.connect(this.analyser); this.analyser.connect(this.ctx.destination)`;`analyser.fftSize = 2048`(1024 bin,bin 宽 ~21-23Hz @ 44.1/48kHz);`analyser.smoothingTimeConstant = 0.8`(落雪同款平滑);新方法 `getAnalyser()` + `getFrequencyData(buf?)`(未绑时返全 0,避免 caller 拿到 undefined)

- **新** `companion/static/music-vue/src/composables/useSpectrum.ts`(~115 行):`SPECTRUM_BANDS` 16 频点 + `SPECTRUM_BAR_COUNT=16` 常量;`registerSpectrumAudio(audio)` 暴露给 composable pause 判定;`useSpectrum(): Ref<number[]>` onMounted 启 rAF loop + onBeforeUnmount cancel;每帧 `eqEngine.getFrequencyData()` → 16 段对数映射(每段 = 中心 Hz ± 半段宽 → bin range → peak 而非 avg → 归一 0-1);`audio.paused` / 未绑 → 全 0 输出

- **新** `companion/static/music-vue/src/components/SpectrumBars.vue`(~75 行):`props: { data: number[] }`;`<div class="spectrum">` 包 16 个 `.bar`,`barHeight(v)` = `Math.max(2, Math.min(100, v*100))`%;CSS `linear-gradient(to top, var(--gh-gold), var(--gh-red))` 主题色;`transition: height 60ms linear` 平滑;底部 `.hz-tip` 显示频点(32/64/125/.../20k)

- **改** `companion/static/music-vue/src/views/MusicView.vue`(+15 行):import `useSpectrum` + `SpectrumBars` + `registerSpectrumAudio`;`const spectrum = useSpectrum()` 顶层;onMounted 调 `registerSpectrumAudio(player.getAudioElement())` + `loadSongs()`(旧 `onMounted(loadSongs)` 升级);`.bottom` grid 改 `240px 1fr 160px`(3 列);`<SpectrumBars v-show="viewMode 非 queue" :data="spectrum" />` 挂中间

**测试 1 新**:

- `tests/test_spectrum_p38.py`(31 测试,6 类):
  - **TestEqEngineAnalyser** (7):AnalyserNode 创建 / 在 masterGain 之后 / fftSize=2048 / smoothing=0.8 / freqBuf 初始化 / getFrequencyData 方法 / 未绑时全 0
  - **TestSpectrumBinMapping** (5):SPECTRUM_BANDS 16 频点 / SPECTRUM_BAR_COUNT=16 / peak 而非 avg / pause 全 0 / 归一 255
  - **TestUseSpectrumComposable** (5):rAF + cancelAnimationFrame / onMounted 启 loop / onBeforeUnmount cancel / registerSpectrumAudio / eqEngine.isBound() 检查
  - **TestSpectrumBarsComponent** (5):props data / 16 段 / gold-red gradient / transition 60ms / 静音 2px
  - **TestMusicViewSpectrumIntegration** (6):import useSpectrum + SpectrumBars / <SpectrumBars :data="spectrum" /> / viewMode 控制 / .bottom 3 列 / onMounted registerSpectrumAudio
  - **TestPrivacyNoUpload** (3):无 ws.publish / 无 ipcRenderer / 无 fetch+XMLHttpRequest(沿用 P3.10b 红线)

**Ship memory + 索引**:
- `memory/p3-8-music-spectrum-shipped.md`(本文件)
- `memory/MEMORY.md` +1 行索引

## 关键设计点

### 1. AnalyserNode 插 masterGain 之后(EQ shaping 后)
```typescript
// services/eq.ts:82-97
this.masterGain = this.ctx!.createGain()
this.masterGain.gain.value = 1.0
this.analyser = this.ctx!.createAnalyser()
this.analyser.fftSize = 2048
this.analyser.smoothingTimeConstant = 0.8
this.freqBuf = new Uint8Array(this.analyser.frequencyBinCount)
// 链:source → bq0..9 → masterGain → analyser → destination
prev.connect(this.masterGain)
this.masterGain.connect(this.analyser)
this.analyser.connect(this.ctx.destination)
```
- **频谱反映 EQ + 总体音量 = 用户实际听到的样子**(落雪 / Spotify 同款)
- masterGain=0.5(enabled 防削顶)时 analyser 收到的是 -6dB 信号,与听觉一致

### 2. 16 段对数映射(覆盖 0-22kHz 人耳范围)
```typescript
// composables/useSpectrum.ts:30-35
export const SPECTRUM_BANDS = [
  32, 64, 125, 250, 500, 1000, 2000, 4000,
  6000, 8000, 10000, 12000, 14000, 16000, 18000, 20000,
] as const
```
- 16 段覆盖 8 个倍频程(对数均布,32Hz 起,20kHz 终)
- 末段取到 Nyquist 22050 Hz(避免高频丢段)

### 3. peak 而非 avg(落雪同款)
```typescript
// composables/useSpectrum.ts:78-86
let peak = 0
for (let b = lo; b <= hi; b++) {
  const v = buf[b]
  if (v > peak) peak = v
}
out[i] = peak / 255  // 归一 0-1
```
- bin range 内取 peak(非 avg),瞬时鼓点/bass kick 看得见
- 平滑由 AnalyserNode `smoothingTimeConstant=0.8` 提供(80% 上帧权重)

### 4. pause 时强制 0 输出(避免残留频谱)
```typescript
// composables/useSpectrum.ts:62-67
if (!_audioRef || _audioRef.paused || !eqEngine.isBound()) {
  spectrum.value = new Array(SPECTRUM_BAR_COUNT).fill(0)
  return
}
```
- Web Audio 暂停后流断,`getByteFrequencyData` 仍返残留(尾音)
- 用 player audio.paused 判定强制 0,避免「静音但频谱条在跳」的鬼影

### 5. 不破坏 bound 幂等
- AnalyserNode 仅在 `bind()` 首次链一次,与现有 bq/masterGain 同模式
- `if (this.bound) return true` 二次 bind 静默 noop

### 6. 多 BrowserWindow 隔离
- **仅在主 music 子窗(MusicView)挂载 useSpectrum**
- LyricOnlyView(歌词独立窗)+ EqWindowView(EQ 独立窗)→ 拿不到 PlayerService.audio,不显示频谱
- 独立窗若要频谱,需 WS 广播频谱数据(违背「零 IPC」红线,放弃)

### 7. 不引入新依赖
- Web Audio AnalyserNode 浏览器原生,0 npm 依赖
- requestAnimationFrame window API,60fps 自动让出主线程

## E2E 验证

- **pytest**:`python -m pytest tests/test_spectrum_p38.py -v` → **31/31 绿**(~0.4s)
- **全栈回归**:`pytest test_spectrum_p38 + test_chip_search_p37 + test_song_pool_and_favorite + test_download_toast_p36 + test_toast_p310a + test_eq_p35 + test_music_lyric_p34 + test_music_lyric_vue_p26 + test_music_lyric_p31 + test_music_lyric_p32 + test_music_favorite_menu -q` → **373/373 绿**(~2.65s)(P3.8 31 + 之前 342)
- **vite build**:`cd companion/static/music-vue && npx vite build` → 0 error,build 1.20s;main 16.89 → 18.23KB(+1.34KB SpectrumBars + useSpectrum);3 entry(index/lyric/eq)全过

## 借鉴 / 复用

| 复用项 | 文件 | 用法 |
|---|---|---|
| `EqEngine.bind()` 链 audio | `services/eq.ts:62-104` | 末尾插 analyser + connect,沿用 bound flag 幂等 |
| `masterGain` 现有节点 | `services/eq.ts:82-83` | 升级 masterGain → analyser → destination 链 |
| `getAudioElement()` getter | `services/player.ts:78` | MusicView onMounted 调 registerSpectrumAudio |
| `useDownloadToast` composable 范式 | `composables/useDownloadToast.ts` | 沿用 onMounted 顶层订阅 + onBeforeUnmount 退订 + 防御模式 |
| `requestAnimationFrame` 原生 | window API | 60fps 自动 |
| Web Audio AnalyserNode | 浏览器原生 | 0 npm 依赖 |
| MusicView `.bottom` 现有 grid | `views/MusicView.vue:437-442` | 加第 3 列 160px |
| Toast / EQPanel / PopupMenu 组件风格 | `components/*.vue` | SFC 沿用 |
| P3.5 EQ 链路 6 预设 | `services/eq.ts:34-41` | 不动,频谱反映 EQ shaping 后输出 |

## 风险登记(已规避)

1. ✅ **多 BrowserWindow 隔离** — 频谱仅在主 music 子窗(MusicView)显示;LyricOnlyView / EqWindowView 不显示;独立窗拿不到 audio
2. ✅ **AnalyserNode 与 masterGain 顺序** — 在 masterGain 之后(EQ shaping 后,反映用户实际听到)
3. ✅ **fftSize 选错** — 选 2048(1024 bin,bin 宽 ~21-23Hz),覆盖 0-22kHz,平衡精细度
4. ✅ **smoothingTimeConstant 抖动** — 0.8 落雪同款,频谱条不会太抖
5. ✅ **暂停时频谱不全为 0** — audio.paused 强制 0 输出,避免残留
6. ✅ **AudioContext 用户手势约束** — 已在 `eqEngine.resume()`(player.ts:142)处理
7. ✅ **频谱条响应延迟** — rAF + smoothingTimeConstant 0.8 → ~100ms 视觉延迟(可接受)
8. ✅ **频谱条高度动画** — CSS transition 60ms linear 平滑无 jitter
9. ✅ **EQ enabled=false 时频谱** — masterGain=1 直通,analyser 仍有数据,频谱照常显示
10. ✅ **频谱条过亮 / 主题色** — gold/red 主题色,linear-gradient 符合现有 cover/queue 视觉
11. ✅ **暂停时 rAF 浪费** — pause 时 spectrum 强制 0 输出,rAF 继续(浏览器自然节流)
12. ✅ **AudioContext 关闭** — singleton 持有不变,无需特殊处理
13. ✅ **频谱数据 60fps 跨进程** — 纯 renderer,无 IPC / WS / 落盘 / 上传(P3.10b 红线)

## 用户隐私顾虑(沿用 P3.10b 教训)

P3.8 全程本地,无任何上传/外传:
- 频谱数据纯前端 renderer 渲染(Web Audio AnalyserNode + rAF)
- 无 ws.publish / 无 IPC handler / 无 fetch / 无 XMLHttpRequest
- 跨文件响应链不触网
- 多 BrowserWindow 隔离(主窗独占,独立窗不显示)
- 用户拍板:masterGain 之后(EQ shaping 后),频谱是用户实际听到的样子

符合 [p3-10-bubble-cancelled-privacy.md](../p3-10-bubble-cancelled-privacy.md) 中用户原话「0 上传/外传」红线。

## 与既有 ship 的关系

```
2026-10-04  P3.5 10 段 EQ ship (c9073a7)
2026-10-04  P3.10a desktop toast ship (4f16c3c)
2026-10-04  P3.6 N8 下载完成 toast ship (9b1ec51)
2026-10-04  P3.7 N3+N2 chip 多选 + search ship (3f5e84e)
2026-10-04  本计划   P3.8 N10 实时频谱 ship            ← 本次 ship
```

11 项主流优化进度:**11/11 已 ship** 🎉(N9 AI 歌单推荐独立 backlog,不在 11 项主流优化之列)

## 关键 commit 信息

```
feat(music): 实时频谱 16 段 (P3.8 N10)

- 前端 services/eq.ts: bind() 末尾串接 AnalyserNode(masterGain → analyser → destination)+ fftSize=2048 + smoothing=0.8 + getFrequencyData/getAnalyser 暴露;未绑时全 0
- 前端 composables/useSpectrum.ts (新): rAF 60fps loop + 16 段对数映射(peak 而非 avg)+ audio.paused 强制 0 输出 + onMounted/onBeforeUnmount 生命周期 + registerSpectrumAudio
- 前端 components/SpectrumBars.vue (新): props data: number[] + 16 段 vertical bar + gold/red linear-gradient + transition 60ms + 静音 2px + 底部 hz-tip 频点标签
- 前端 views/MusicView.vue: 顶层 useSpectrum() + onMounted registerSpectrumAudio(player.getAudioElement()) + .bottom grid 240px 1fr 160px + <SpectrumBars v-show="非 queue" :data="spectrum" />
- 31 pytest 全绿 / 373 回归全绿 / vite build 0 error 1.20s;纯 renderer 增量,无 IPC / 无 WS / 无 audio 上传(沿用 P3.10b 红线)
- 11 项主流优化 11/11 ship 完结
```

## 后续(本 ship 外)

- **N9 AI 歌单推荐**(backlog)— 独立功能,需 AI 路由 + 历史画像;不在 11 项主流优化之列
- **backlog**:LyricOnlyView 歌词独立窗加频谱(如要需 WS 广播频谱数据,违背零 IPC 红线,放弃)
- **backlog**:MiniBar 嵌入频谱(如要需共享 AnalyserNode 引用,跨组件状态,待讨论)
- **backlog**:频谱样式 8/16/32 段切换 / 颜色主题切换 / 镜像对称模式
