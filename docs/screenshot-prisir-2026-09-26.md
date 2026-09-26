# screenshot-mcp × PrisirAI 整合设计(2026-09-26 ship, P3j T28)

> 状态:**shipped**
> 标签:P3j T28 / joeblack-lha/screenshot-mcp / 桌面截图
> 跟 T25/T26 浏览器内截图的差异:T28 能截「整个桌面 / 任意窗口 / 指定区域」
> 补完用户真实场景(微信/钉钉/游戏/IDE 等桌面应用界面)

---

## 1. 背景

T25 Playwright MCP / T26 agent-browser 都是**浏览器内截图**,只能截当前页面的 viewport。
但用户真实场景里大量需要的是:
- 看微信/钉钉/Teams 等桌面 IM 的对话
- 看 IDE/终端/资源管理器等窗口内容
- 看游戏画面 / 视频播放器
- 看截图应用/绘图软件等的自定义窗口

**joeblack-lha/screenshot-mcp**(npm 全平台 MCP server + CLI,~2026 头部
screenshot MCP)直接调原生后端:
- **Windows**:PowerShell + System.Windows.Forms(系统自带)
- **macOS**:screencapture(/usr/sbin 系统自带)
- **Linux**:grim(Wayland)/ scrot / maim / import(X11)优先级探测

输出 PNG 默认存 `~/.screenshot-mcp/captures/`,可指定 `--output-dir`。

PrisirAI 拿到截图路径后,**通过 `capability_exec_result` 链路自动送给 LLM
vision**(若 LLM 多模态已开),实现「LLM 看图」,闭环用户指令如:
「帮我看看微信现在有什么未读」、「把 VSCode 当前界面截图给我」。

---

## 2. 架构决策

### 2.1 协议选择(跟 T25/T26 都不同)

| | T25 Playwright MCP | T26 agent-browser | **T28 screenshot-mcp** |
|--|--|--|--|
| 协议 | stdio JSON-RPC 长连 | batch subprocess CLI(Rust daemon) | **batch subprocess CLI(无 daemon)** |
| 常驻 | 是 | 否(Rust daemon 自长驻) | **否**(纯单进程调起) |
| 浏览器依赖 | Chromium ~200MB | Chrome for Testing ~150MB | **无浏览器**(调系统 API) |
| 截图范围 | 浏览器渲染页面 | 浏览器渲染页面 | **整个桌面 / 窗口 / 区域** |

**选 batch subprocess CLI** 的原因:
- screenshot-mcp CLI 自带平台原生后端适配,无 Chromium 启动开销
- 每次调用 < 200ms(Win PowerShell 截图实际 100ms 内)
- 跟 gh_bridge / agent_browser_bridge 模式完全一致

### 2.2 健康模式(4 档)

| mode | 触发条件 | 前端建议 |
|------|---------|---------|
| `missing_cli` | `screenshot-mcp` 不在 PATH | 跳 npm 安装 |
| `missing_node` | Node.js < 18(screenshot-mcp 要求) | 提示装 Node ≥ 18 |
| `no_backend` | 当前平台无原生后端(Linux 未装 grim/scrot) | 提示 sudo apt install grim |
| `ready` | 全 OK | 可用 |

### 2.3 截图模式(3 档)

```bash
screenshot-mcp capture --mode fullscreen          # 全屏
screenshot-mcp capture --mode window              # 当前焦点窗口
screenshot-mcp capture --mode area --area "x,y,w,h"   # 指定区域
screenshot-mcp capture --filename cap.png         # 自定义文件名
screenshot-mcp capture --output-dir /path/to      # 自定义输出目录
```

输出 stdout 含 `Saved to: <path>`,bridge 用正则 `Saved\s+to[:\s]+(\S+)` 抽路径。

### 2.4 风险等级(全部 L0)

| 操作 | 风险 | 前端 UX |
|------|------|---------|
| `ss_health / ss_capture / ss_list / ss_read / ss_active_backend / ss_install_hint` | **L0 只读** | 直通 |

截图本质不破坏数据(只是写入新文件),但属用户环境副作用 — 跟 T26 agent-browser 的
ab_click / ab_fill(L1)不同,截图本身不需要用户逐次确认。

### 2.5 防穿越与边界保护

- `area` 格式校验:`x,y,w,h` 全部非负整数、宽高 > 0
- `filename` 防穿越:不允许 `/` `\` `..`
- `path` 读图先 `Path.exists()` 再 stat(防 TOCTOU 误读)

---

## 3. 实现概览

### 3.1 新建模块

- [`prisir_work/screenshot_bridge.py`](../prisir_work/screenshot_bridge.py) ~491 行
  - 6 公开 API:`ss_health / ss_capture / ss_list / ss_read / ss_active_backend / ss_install_hint`
  - 内部 `_run(args, *, timeout, need_json)` 复用 `gh_bridge._run` 模式
  - `_detect_platform()` 返 `windows/darwin/linux`
  - `_detect_backend()` 返 `(backend_name, available)` — 4 档 mode 的核心
  - `_parse_saved_path(stdout)` 正则抽 `Saved to: <path>`
  - `_read_png_dimensions(f)` / `_read_jpeg_dimensions(fp)` 解析图片头 8 字节拿尺寸

### 3.2 端点(6 个)

| path | method | risk | 用途 |
|------|--------|------|------|
| `/web/screenshot/health` | POST | L0 | 探活(4 档 mode) |
| `/web/screenshot/capture` | POST | L0 | 桌面截图主操作 |
| `/web/screenshot/list` | POST | L0 | 列已保存截图 |
| `/web/screenshot/read` | POST | L0 | 读截图元数据 |
| `/web/screenshot/active_backend` | POST | L0 | 当前平台 + 后端 |
| `/web/screenshot/install_hint` | POST | L0 | 安装提示 |

### 3.3 能力(6 个 `web.screenshot.*`)

| capability id | risk | 触发关键词 |
|--------------|------|-----------|
| `web.screenshot.health` | L0 | screenshot 状态 / 健康 / 探活 / 截图 mcp 健康 |
| `web.screenshot.capture` | L0 | 截图 / 截屏 / 拍屏幕 / 桌面截图 / 截全屏 / 截窗口 / 截区域 / 看屏幕 / 截个图给我看看 |
| `web.screenshot.list` | L0 | 列截图 / 历史截图 / 最近截图 / 之前截的图 |
| `web.screenshot.read` | L0 | 看截图信息 / 截图尺寸 / 截图大小 |
| `web.screenshot.active_backend` | L0 | 截图后端 / 看后端 |
| `web.screenshot.install_hint` | L0 | screenshot 装法 / install hint / npm install |

### 3.4 错误翻译(9 条 `ss_*`)

| 错误 | 翻译 | link |
|------|------|------|
| `ss_cli_not_found` | "screenshot-mcp 未装(跑 `npm install -g screenshot-mcp`)" | npm |
| `ss_timeout` | "screenshot-mcp 操作超时(可重试,或加大 timeout 参数)" | /extensions |
| `ss_no_backend` | "当前平台截图后端缺失(Win 装 PowerShell / Linux 装 grim 或 scrot / macOS 必有 screencapture)" | /extensions |
| `ss_invalid_area` | "area 格式错(必须 'x,y,w,h' 非负整数,宽高 > 0)" | — |
| `ss_failed` | "screenshot-mcp 截图失败(看 stderr 末尾 200 字符;权限/分辨率/后端都可能)" | /extensions |
| `ss_bad_mode` | "mode 必须是 fullscreen \| window \| area" | — |
| `ss_bad_filename` | "filename 不能含路径分隔符或 '..'(防穿越)" | — |
| `ss_not_found` | "截图文件不存在(可能被清理或路径错)" | /extensions |
| `ss_node_too_old` | "Node.js < 18,screenshot-mcp 要求 ≥ 18" | nodejs.org |

---

## 4. 跟 T25/T26 的边界

| 用例 | 推荐 |
|------|------|
| 截浏览器内渲染的页面(填表后看效果) | T25/T26(playwright/agent-browser) |
| 截整个桌面 / 当前窗口 / 自定义区域 | **T28(screenshot-mcp)** |
| 截微信/钉钉/游戏/IDE 等桌面应用 | **T28** |
| 截屏后 LLM vision 看图 | **T28**(T28 输出 PNG 自动进 vision 链路) |
| 静态抓取 / 防反爬 | web_fetch / jina / feedparser |

**三者并存** = 浏览器内(T25/T26)+ 桌面外(T28),覆盖全部截图场景。

---

## 5. E2E 验证

### 5.1 单元测试

```bash
python -m pytest tests/test_screenshot_bridge.py tests/test_screenshot_endpoints.py -v
# 期望:21 + 14 = 35/35 绿
```

### 5.2 verify 全套

```bash
python verify_wechat_publisher.py -SkipHttp
# 期望:68/68(64 既有 + 4 new ss check)
```

### 5.3 真机 E2E(需 npm + screenshot-mcp)

```bash
# 装 screenshot-mcp
npm install -g screenshot-mcp

# 探活
python -c "from prisir_work import screenshot_bridge as ss; print(ss.ss_health())"
# → {"mode": "ready", "backend": "powershell", "platform": "windows", ...}

# 主对话 E2E:
# 「帮我截个全屏看看现在屏幕」
# → [[EXEC: web.screenshot.capture mode="fullscreen"]]
# → ss_capture → subprocess.run → 截图保存到 ~/.screenshot-mcp/captures/
# → {ok: True, path: "/path/to/cap.png"}
# → 自动送给 LLM vision(若多模态开)

# 真截区域:
# 「截屏幕左上角 800x600」
# → [[EXEC: web.screenshot.capture mode="area" area="0,0,800,600"]]
```

---

## 6. 风险与缓解

| 风险 | 缓解 |
|------|------|
| screenshot-mcp 未装 / `npm install -g` 失败 | ss_health mode=missing_cli + npm 安装提示 |
| Node.js < 18 | mode=missing_node + 装 https://nodejs.org/ |
| 当前平台无后端(Linux 没装 grim/scrot) | mode=no_backend + 装 grim 提示 |
| 用户误传非法 area | ss_invalid_area → 提示格式 |
| 用户传路径穿越 filename | ss_bad_filename → 拒绝 |
| 截图分辨率超 LLM vision 上限(20MB+) | T28 仅返 path;前端按需压缩 / 转 base64 |
| 截图含敏感信息(密码/私聊) | T28 截图本身 L0 直通;用户需自行评估风险 |
| 跟 T25/T26 capability id 撞 | 命名空间分开:`web.screenshot.*` vs `web.playwright.*` / `web.agent-browser.*` |

---

## 7. Rollback

- `prisir_work/screenshot_bridge.py` 文件删除
- `prisir_work/endpoints.py` 删 6 行 `@register`(/web/screenshot/*)
- `prisir_work/capability.py` 删 6 行 `register_capability`(web.screenshot.*)
- `prisir_work/agent_main_chat_hook.py` 删 9 行翻译(ss_*)
- `tests/test_screenshot_*.py` 文件删除
- `verify_wechat_publisher.py` 删 4 check + 4 CHECKS 行
- `docs/screenshot-prisir-2026-09-26.md` 文件删除
- `memory/screenshot-prisir.md` + MEMORY.md 删 1 行

---

## 8. 何时重新评估

- screenshot-mcp 出 v2(MCP server + CLI 双形态)→ 评估接 stdio JSON-RPC
- 多模态 LLM(vision)普及 → 加 `web.screenshot.recognize_text` (OCR 抽文本)
- 用户反复截大图 → 加自动压缩 + 缩略图
- macOS screencapture 命令改 → 重新适配

---

## 9. 跨能力边界一览(决策表)

| 信息源 | 何时用 |
|--------|-------|
| web_fetch | 静态 HTML,免 key |
| web_fetch_jina | 防反爬 / 全文搜索 |
| gh_bridge | GitHub 元数据/issue/PR/搜索 |
| feedparser | RSS/Atom |
| yt-dlp | 200+ 网站 metadata + 字幕 |
| exa | 语义搜索(需 EXA_API_KEY) |
| hackernews | HN 帖子 |
| agent-reach | 14 平台读/搜 |
| agent-browser(T26) | 多步浏览器交互 + 稳定 refs |
| playwright-mcp(T25) | 长连浏览器会话 |
| **screenshot-mcp(T28)** | **桌面 / 窗口 / 区域 截图** |
| video creators | 一键出片 / 视频处理 |
| music | 音乐能力 |
| publisher | 多平台发布 |

---

## 10. 数据流(主对话「帮我看看微信现在有什么未读」)

```
用户输入
  ↓ LLM 解析
  [[EXEC: web.screenshot.capture mode="window"]]
  ↓ endpoints._web_ss_capture
  ↓ ss_bridge.ss_capture
  ↓ subprocess.run(["screenshot-mcp", "capture", "--mode", "window"])
  ↓ PowerShell + .NET 截当前焦点窗口 → ~/.screenshot-mcp/captures/timestamp.png
  ↓ 返 {ok: True, path: "/Users/x/.screenshot-mcp/captures/2026-09-26T12-34-56.png"}
  ↓ build_exec_result → ws 事件 capability_exec_result
  ↓ 前端显示 ✓ + 自动调 vision LLM 把 PNG 喂给模型
  ↓ LLM:「我看到微信未读 3 条:1. 张三-下午好...」
  ↓ 反馈用户

[变体] 用户说「截屏幕左上角 800x600」
  ↓ [[EXEC: web.screenshot.capture mode="area" area="0,0,800,600"]]
  ↓ ss_bridge.ss_capture → 校验 area → subprocess.run
  ↓ 截区域 → 返 path → vision LLM 看
```

---

## 11. Commit 信息(2026-09-26)

- `feat(p3j t28): joeblack-lha/screenshot-mcp 接入(桌面截图,补完浏览器外场景)`
- 共 7 文件 + 1 修改,~1698 行(其中 bridge 491 行 / 测试 600+ 行 / verify 4 check)
- 21 bridge tests + 14 endpoint tests = 35/35 绿
- verify 全套 68/68 绿(64 既有 + 4 new ss check)