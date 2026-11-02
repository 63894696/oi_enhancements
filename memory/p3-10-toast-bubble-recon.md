# P3.10 桌面弹卡 + 音乐识别浮泡 侦察

## A. 桌面弹卡 toast

### 主流客户端调研表

| 客户端 | 位置 | 形态 | 自动消失 | 手动关闭 | 堆叠策略 |
|---|---|---|---|---|---|
| **Spotify Web/Desktop** | 右下角 | 圆角卡片 360×80, 半透明深底 + cover 图 | 4-6s | ✕ 按钮 + 整卡点击 | 垂直堆叠,上限 3 个,旧上推 |
| **QQ 音乐** | 右下角 | 系统 Notification(Win10/11 toast),非自定义 | 系统控(5-8s) | 系统按钮 | 系统队列,应用不控 |
| **Apple Music(Mac)** | 顶部右侧 | 系统 Notification Center 横幅 | 系统控 | 系统 | 仅最新 1 条 |
| **Telegram Desktop** | 左下角 | 圆角卡片 + 头像 + 标题 + 摘要 | 5s,可设永不消 | ✕ 按钮 | 垂直堆叠,上限 5 |
| **Steam** | 右下角 | 圆角卡片 + 图标 + 标题 + 进度条 | 完成/手动 ✕ | ✕ 按钮 + 滑动关闭 | 单卡替换 |
| **VS Code** | 右下角 | 圆角小卡片 320×56 | 4s,鼠标悬停暂停 | Dismiss 按钮 | 垂直堆叠,上限 3 |
| **macOS 系统** | 顶部右侧 | 圆角胶囊 + app icon + 标题 | 系统控(常驻直到点) | ✕/悬停展开 | 系统队列 |

观察结论:桌面应用**主流 = 右下角**(Win/Mac 默认);macOS 系统级走顶部右侧横幅;Steam / VS Code / Spotify / Telegram 全部右下角 = PrisirAI 应跟随右下角。

### 位置选择建议

**推荐:右下角**(适配 Win 默认托盘区 + Mac 屏幕右下空白)

理由:
1. PrisirAI 已有 5 个 _CHILD_SPEC 子窗(语伴/音乐/日历/工作流/歌词),歌词窗 alwaysOnTop 右下,可与 toast 同区域共存(垂直堆叠,toast 在歌词上方 16px)
2. 顶部右侧 = Mac 系统横幅区,会与系统级通知重叠混淆
3. 底部中间(Slack 风)= 与聊天/音乐子窗遮挡(用户开 music 子窗时,中央 toast 遮播放按钮)
4. 左下角 = 主流客户端(Telegram)不选,跟 Win 托盘位置错位

**位置参数化**:`position: 'bottom-right' | 'top-right' | 'bottom-center' | 'tray-adjacent'`,默认 `bottom-right`。

### 多 toast 堆叠建议

**垂直堆叠,上限 3 个** = Spotify/VS Code 同款
- 第 4 个来 → 旧最上的消失
- 同 ID 重复(同 action + 同 key)在 TTL 内去重(避免疯狂弹)
- 持久化 `toast_max_visible: 3` 给用户改

### 动画 3 段细节

| 段 | 时长 | 缓动 | 效果 |
|---|---|---|---|
| **Enter** | 220ms | `cubic-bezier(0.16, 1, 0.3, 1)` (ease-out-expo) | `translateX(0) → translateX(20px)` 反向回 0 + opacity 0→1 |
| **Hold** | TTL 4s(可配) | — | 鼠标悬停暂停 setTimeout 计时 |
| **Exit** | 180ms | `cubic-bezier(0.7, 0, 0.84, 0)` (ease-in) | opacity 1→0 + scale 1→0.96 + translateY 0→-4px |

实现:`window.requestAnimationFrame` + Web Animations API(优先) / CSS transition 备选。Hover 暂停 = 在 `mouseenter` 暂停 setTimeout setTimeout(剩 TTL);`mouseleave` 重启 setTimeout。

### OS 兼容矩阵

| 属性 | Windows | macOS | Linux |
|---|---|---|---|
| `transparent: true` | ✓ 需 DWM | ✓ | ✓(部分 WM 需配) |
| `frame: false` | ✓ | ✓ | ✓ |
| `alwaysOnTop: true` | ✓ 等同 'floating' | ✓ 但需 `setAlwaysOnTop(true, 'screen-saver')` 才不被遮 | ✓ 走 _NET_WM_STATE_ABOVE |
| `skipTaskbar: true` | ✓ | ✕(Mac 无 taskbar) | ✓ 部分 DE |
| `focusable: false` | ✓ 隐式 skipTaskbar=true | ✓ 不抢 focus | ✓ 禁 WM 交互 |
| `hasShadow: false` | ✕ 无效 | ✓ 关掉原生系统层 shadow | ✕ 无效 |
| `setVisibleOnAllWorkspaces(true)` | ✕(单 workspace) | ✓ 多 space 显示 | 部分 WM ✓ |

PrisirAI 主战场 Windows,Win 上 alwaysOnTop 稳。Mac setLevel='screen-saver' 必须设,Linux 多数 WM 走 _NET_WM_STATE。

### Electron API 关键参数

```js
new BrowserWindow({
  width: 360, height: 88, minWidth: 280, maxWidth: 480,
  frame: false,
  transparent: true,
  backgroundColor: undefined,           // 透明窗不能设 backgroundColor
  alwaysOnTop: true,
  resizable: false,
  movable: false,                       // toast 不让用户拖
  skipTaskbar: true,
  focusable: false,                     // 关键:不抢主窗焦点
  hasShadow: false,                     // Mac 关原生 shadow(我们用 CSS shadow)
  show: false,                          // ready-to-show 再 show
  webPreferences: {
    preload: path.join(__dirname, "preload.js"),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
  },
})
// ready-to-show 后定位屏幕右下角
const { workArea } = screen.getPrimaryDisplay();
w.setPosition(workArea.x + workArea.width - 360 - 16,
              workArea.y + workArea.height - 88 - 16);
// Mac 提升到 screen-saver 层(防被 Safari/Chrome 浮窗遮)
w.setAlwaysOnTop(true, "screen-saver", 1);
```

### PrisirAI 现状:已有页面 toast,但不是 OS 级

- `companion/static/music-vue/src/components/Toast.vue` = **页面内** top-center toast,2440ms TTL
- `companion/static/music-vue/src/stores/ui.ts` pushToast/pushListToast
- **结论**:OS 级 toast 子窗 = 新增,不替换现有页面 toast(后者用于子窗内反馈,前者用于跨子窗全局通知)

---

## B. 音乐识别浮泡 floating bubble

### 主流客户端调研表

| 客户端 | 位置 | 形态 | 状态机 |
|---|---|---|---|
| **Shazam(macOS)** | menubar item | 圆角方块 28×28 in menubar | 收听中(脉冲圈) → 识别成功(显示曲目+展开卡) |
| **网易云音乐「听歌识曲」** | 右下角悬浮球 60×60 | 圆形 + 🎵 图标 + 收听中脉冲 | 点击 → 收听 → 进度环 → 结果卡(曲目+艺人+操作按钮) |
| **QQ 音乐内置** | 中央模态 | 圆形 + 波形条 | 收听 → 识别中(spinner) → 结果 |
| **SoundHound** | 独立 app + 浮窗 | 居中圆 + 波形可视化 | 收听 → loading → 结果卡 |
| **Apple MusicKit(macOS)** | menu bar | 圆形 + 麦克风 | 同 Shazam |
| **酷狗音乐内嵌** | 右下角浮球 | 圆形 + 「听歌识曲」字 | 点击 → 收听 → 结果列表 |

观察:Shazam menubar 风格最克制,网易云浮球最贴近「右下角随手用」。

### 气泡位置建议

**推荐:托盘图标右侧(模仿 Shazam menubar)**
- 主屏底部右侧 24px 偏移
- 位置参数化:`bubble_position: 'tray-adjacent' | 'bottom-right' | 'center'`,默认 `'tray-adjacent'`
- 移动距离 0(不拖),固定位置弹出(识别是瞬时操作,用户不需要挪动)

### 状态机

```
        click tray         capture start        api success
[Idle] ────────────────▶ [Listening] ────────▶ [Recognized]
   ▲                       │  ◀──── 1s 后自动重采 ────┐
   │                       ▼                          │
   │                   [Recognizing] ───────────────▶┘
   │                       │ api error
   │                       ▼
   │                   [Failed] ───── 3s 后自动消
   │
   └─────── 10s 无操作 / 用户点 ✕ ──── any state ──▶ [Dismissed] (close)
```

5 态 idle/listening/recognizing/recognized/failed,加瞬态 dismissed。

### 音频捕获方案对比

| 方案 | 优点 | 缺点 | 推荐度 |
|---|---|---|---|
| **Web `getUserMedia({audio:true})` + MediaRecorder** | Electron 内置 Chromium 支持,免依赖;3 行代码;wav/webm 直出 | 浏览器要弹权限窗(一次性授权);Win 上需 WASAPI 默认输入设备 | ★★★ |
| **`desktopCapturer` + Electron `webContents.capturePage`** | 可截全屏音频 | 不支持音频(仅视频);不可行 | ✕ |
| **NAudio(.NET)/pyaudio(Win)** | 系统级,可直接捕 loopback(系统声音) | PrisirAI 后端 Python 已 OK,但用户麦克风授权更直接 | ★(备选) |
| **WASAPI loopback via Windows COM** | 直接捕系统播放(无需让用户开麦克风) | Win 专属,需 pywin32;macOS/Linux 走不同路径 | ★★(进阶) |

**推荐 Phase 1:Electron bubble 子窗内 `getUserMedia({audio:true})` + `MediaRecorder`(webm/opus 容器)→ 转 wav → POST `/api/recognize`**。

理由:
1. 用户点托盘「🎵 听歌识曲」→ bubble 冒出来(已授权用户 0 延迟,首次弹权限窗一次)
2. 麦克风采集 ≈ 用户「对着电脑哼」「手机外放」都收
3. 局限性:macOS Catalina+ 需 entitlement `com.apple.security.device.audio-input`(装包后 NSIS 需注入)
4. Phase 2 加 WASAPI loopback 捕系统播放(用户在 PrisirAI 听歌时,不需开麦克风)— 但 Phase 1 不做,保持复杂度低

### 识别 API 3 候选对比表

| API | 免费额度 | 上传/URL | 准确性(英文/中文流行) | Python SDK | 备注 |
|---|---|---|---|---|---|
| **AudD** | ~1000 次/月(也看到 30/天 报告,**需 WebSearch 二次确认**);付费 $5/月起 | URL(YT/Apple Music/Deezer)免费;**音频文件直传需付费** | 中等(英文强,中文覆盖 ~70%) | 有 `requests` 示例;无官方 Python lib | **推荐 P0 试音 / 发送链接流媒体 URL** 路线。限制:free tier 不能传本地音频 |
| **ACRCloud** | **14 天试用**;免费档 2000 次/月(需信用卡) | 音频文件直传 ✓ | 优秀(中文流行 90%+);商用默认 | 官方 Python SDK `acrcloud-sdk-python` | **最准但限制商用**;需试音:用户传 5s 片段 |
| **ShazamKit** | 仅 macOS 13+;**Apple 开发者账号(年费 $99)** | 系统音频 / 内置 | 行业标杆 | 无 Python SDK,需 Swift/ObjC | **仅 Mac**;PrisirAI 主 Win → 不推荐 |

**PrisirAI 推荐路线**(白嫖/自建偏好 + 主 Win):
1. **首选 AudD** — 免费够用,JSON API 简单,Python `requests` 即可;Phase 1 仅支持「粘贴 Spotify/Apple Music/YT 链接」识别(非麦克风)
2. **备选 ACRCloud** — 用户「传音频文件识别」走 ACRCloud 2000 次/月免费档
3. **Phase 2 升级** — 接 mic capture(getUserMedia),录 5-10s → POST to AudD / ACRCloud,视政策迭代

### 气泡 UI 3 风格对比

| 风格 | 视觉 | 代表 | 推荐 |
|---|---|---|---|
| **Spotify 风** | 圆形头像 56px + 旁注(曲目+艺人+进度环) | Spotify Connect | ✓ 优雅,但 60×60 圆不够放文字 |
| **Apple 风** | 中央圆 + 波形可视化(80×80)+ 标题下方 | Shazam/ShazamKit | ★ 配合 WaveSurfer.js 做迷你波形,有「收听中」脉冲反馈 |
| **QQ 风** | 条形 + 文字(横长 240×60)+ 「听歌识曲」字 | QQ 音乐 | ✕ 不够「识别」的科技感 |

**推荐 Apple 风 + Spotify 简版**:
- 收听中:60×60 圆形 + 中心 🎤 图标 + 3 圈向外脉冲 CSS animation(2s loop)
- 识别中:圆形 + CSS spinner 替代脉冲
- 识别成功:260×80 横长卡(圆形缩略图 56×56 + 曲目 + 艺人 + 「收藏/打开/关闭」3 按钮)
- 识别失败:260×60 横长卡 + ⚠️ + 「未识别」 + ✕
- 整体 CSS 主题继承 lyric 窗(国画风变量 `--gh-paper/--gh-ink/--gh-gold`)

### 跨进程通信方案

```
[托盘 click 🎵 识别] 
    ↓ IPC(主进程)
[main.js _recognizeSong()]
    ↓ _openBubbleTrayWindow(label="bubble")
[bubble 子窗: getUserMedia → MediaRecorder 5s]
    ↓ ipcRenderer.invoke("shell:bubbleAudio", base64)
[main.js]
    ↓ aiohttp POST to music web /api/recognize
[prisIragent-music-web.py /api/recognize]
    ↓ (可选) 转发 AudD / ACRCloud
[结果 → bubble 子窗 webContents.send("bubble:result", {title, artist})]
```

新建端点:
- 主进程 IPC: `shell:bubbleAudio`(收音频 base64),`shell:closeBubble`
- music web: `POST /api/recognize`(multipart 上传或 JSON base64 + engine 字段)
- music web WS: 复用 `/ws/state`(加 `recognition_state` 事件类型)— 不新建 WS

---

## C. PrisirAI 集成

### _CHILD_SPEC 扩展字段

```js
const _CHILD_SPEC = {
  companion: { width: 920, height: 680, ... },
  music:     { width: 880, height: 620, ... },
  calendar:  { width: 960, height: 720, ... },
  workflow:  { width: 1000, height: 720, ... },
  lyric:     { width: 720, height: 360, transparent: true, frame: false,
               alwaysOnTop: true, resizable: true, skipTaskbar: true },
  // P3.10 新增
  toast: {
    width: 360, height: 88, minWidth: 280, maxWidth: 480,
    transparent: true, frame: false,
    alwaysOnTop: true, focusable: false,         // ★ 新:不抢焦点
    movable: false, resizable: false,           // ★ 新:toast 不让拖
    hasShadow: false,                          // ★ 新:Mac 关原生 shadow(CSS 接管)
    skipTaskbar: true,
    position: 'bottom-right',                   // ★ 新:右下角定位
    lifetime_ms: 4000,                          // ★ 新:默认 TTL
    max_visible: 3,                             // ★ 新:堆叠上限
  },
  bubble: {
    width: 60, height: 60, minWidth: 60, minHeight: 60,
    maxWidth: 280, maxHeight: 100,              // 听中 60x60;识别成功 260x80
    transparent: true, frame: false,
    alwaysOnTop: true, focusable: false,        // 不抢焦点
    movable: false, resizable: false,
    hasShadow: false, skipTaskbar: true,
    position: 'tray-adjacent',                  // 托盘右侧(主屏右下偏移)
    lifetime_ms: 10000,                         // 10s 无操作自动消
  },
};
```

_createChildWindow 在 P3.10 需加:`focusable: spec.focusable === true`(默认 true 兼容现有子窗),`movable: spec.movable !== false`(默认 true),`hasShadow: spec.hasShadow !== false`(默认 false 让 Mac 用户也能用)。

### music 后端路由扩展

`prisIragent-music-web.py` 加 3 个路由 + 1 个 config:

1. **`POST /api/recognize`** — multipart 上传(音频文件)+ `engine=audd|acrcloud` 字段
   - 调对应引擎 SDK(Phase 1 = audd requests.post + api_token;Phase 2 = acrcloud_sdk)
   - 返 `{ok, title, artist, album, release_date, cover_url, source_url}` 或 `{ok:false, err:"no_match"}`
   - `prisIragent_state.json` 持久化 `recognition_engine`(默认 audd)和 `recognition_api_token`

2. **`GET /api/recognize/config`** — 返当前引擎/token 状态(前端设置页用)

3. **`POST /api/recognize/test`** — 用预置的 5s 音频(系统内置)做连通性测试,给用户「引擎 OK」反馈

不动 `/ws/state`(复用现有 ws);状态事件 `recognition_state` 加在 state payload:`{type:"recording"|"recognizing"|"success"|"failed", ...}`

### 托盘菜单扩展

```js
const multiWindowSubmenu = [
  { label: "📞 语伴",   click: openCompanionWindow },
  { label: "🎵 音乐",   click: openMusicWindow },
  { label: "🎤 桌面歌词", submenu: [...] },   // 已有
  // P3.10 新增
  { label: "🎧 听歌识曲", click: openBubbleWindow },
  // P3.10 新增 — 弹卡设置
  { label: "🔔 弹卡通知", submenu: [
    { label: "启用桌面弹卡", type: "checkbox", checked: _toast_state.enabled, click: _toggleToastEnabled },
    { type: "separator" },
    { label: "弹卡位置", submenu: [
      { label: "右下角", type: "radio", checked: _toast_state.position==="bottom-right", group:"toastPos", click: () => _setToastPosition("bottom-right") },
      { label: "顶部右侧", type: "radio", checked: _toast_state.position==="top-right", group:"toastPos", click: () => _setToastPosition("top-right") },
      { label: "底部中间", type: "radio", checked: _toast_state.position==="bottom-center", group:"toastPos", click: () => _setToastPosition("bottom-center") },
    ]},
    { label: "悬停时暂停计时", type: "checkbox", checked: _toast_state.hover_pause, click: _toggleToastHoverPause },
    { type: "separator" },
    { label: "弹卡设置…", click: openToastSettings },
  ]},
  { label: "📅 日程",   click: openCalendarWindow },
  { label: "🔀 工作流", click: openWorkflowWindow },
  { type: "separator" },
  { label: "关闭所有子窗口", click: closeAllChildWindows },
];
```

### 持久化字段

`prisiragent_state.json`(userData)加 4 字段,UI 调 rebuildTrayMenu 反映 checkbox/radio:

```json
{
  "lyric_window": { "alwaysOnTop": true, "lockDrag": false, "bounds": {...}, "opacity": 0.85, "scale": 1.0, "lines": 1 },
  "toast": {
    "enabled": true,
    "position": "bottom-right",
    "lifetime_ms": 4000,
    "max_visible": 3,
    "hover_pause": true
  },
  "bubble": {
    "position": "tray-adjacent",
    "recognition_engine": "audd",
    "recognition_api_token": "",
    "lifetime_ms": 10000,
    "auto_favorite": false
  }
}
```

---

## 关键决策点(等用户拍板)

1. **toast 位置**:右下角(默认)/ 顶部右侧 / 底部中间
2. **toast 是否可关闭**:✕ 按钮可关 + 整卡点击可关(默认)/ 只能等自动消
3. **toast 悬停暂停**:默认 true / false
4. **识别引擎**:AudD(白嫖 + 流媒体 URL)/ ACRCloud(2000/月 音频文件直传)/ ShazamKit(仅 Mac,排除)
5. **识别 Phase 1 范围**:仅流媒体 URL 粘贴识别 / 含麦克风 getUserMedia 录音
6. **bubble 位置**:托盘右侧(默认)/ 屏幕右下 / 屏幕中央
7. **bubble 音频捕获**:Phase 1 麦克风(getUserMedia)/ Phase 1 系统声音(WASAPI loopback)/ 两者都做
8. **bubble 是否要音频波形可视化**:WaveSurfer.js 迷你波形 / 仅脉冲圆环
9. **bubble 识别成功态**:横长卡 + 收藏/打开/关闭 3 按钮 / 极简(只显示曲目)
10. **持久化配置入口**:仅托盘菜单 / 托盘 + 主 web 设置页 / 托盘 + 弹卡窗内齿轮