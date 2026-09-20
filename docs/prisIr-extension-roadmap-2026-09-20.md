# PrisirAI 扩展能力路线评估 — 2026-09-20

> **背景**:用户 2026-09-20 拍板「先做 Node 编辑器扩展,但在启动前先把路线评估写到 docs/,并先出界面图」。
> 本文 = (1) Node 路线深度评估 (2) Rust+Tauri 路线评估 (3) 跟 PrisirAI 现有架构契合度 (4) 阶段计划 (5) 扩展 API v0.1 草案。
> 配套界面图:[[prisIr-extension-ui-mock-2026-09-20]] — **用户已选 B 轻菜单型(顶栏 +1 按钮 + 右下角抽屉)**。

---

## 0. 当前 PrisirAI 架构快照

| 维度 | 现状 |
|------|------|
| 主入口 | `prisIragent_web.py` 单文件 **11,615 行** |
| 壳 | oiagent-shell(Electron) + Tauri 调研双线进行中([[tav-shell-port-pinning-regression]]) |
| 后端 | Python `ThreadingHTTPServer` + `_handle_*` 派发 + SQLite(`~/.prisir/chat.sqlite3`) |
| API 前缀 | `/prisiragent/api/*`(不是 `/api/*`,坑过) |
| 前端 | 单 HTML + Vanilla JS + 内嵌 CSS,无框架,无构建步骤 |
| 已有的"准扩展"能力 | skill_run(coworker shell 脚本)、doc_panel(本地 Markdown)、calendar、music |
| 权限闸 | v1.0 已落地([[prisIrAI-perm-gate-v1]]) |
| 装包 | PyInstaller → NSIS;**当前阶段不打装包** |

**结论**:扩展能力是「补一层松耦合的注册机制」,**不是重写架构**。Node 路线更省事,Rust+Tauri 路线收益要 6+ 个月才回本。

---

## 1. Node 路线评估(用户倾向)

### 1.1 优势(贴合 PrisirAI 现状)

| 维度 | 评估 |
|------|------|
| **编辑器插件生态** | VSCode Extension API 是事实标准(20+ 年积累),monaco-editor 同源,补「编辑器扩展」能力零学习成本 |
| **npm 生态复用** | `@prisir/extension-sdk`(自写 200 行)+ `esbuild` 打包 100ms 内 |
| **进程模型** | 扩展以 `node` 子进程跑,跟 Python 主进程 stdio JSON-RPC,沙箱天然隔离 |
| **vs PrisirAI 现状契合度** | ★★★★★ — Python 端只多一个 `_handle_extension_*` 派发 + 进程管理器 |
| **渐进交付** | v0.1 跑通 1 个示例扩展(设计稿渲染)只需 2-3 天 |
| **UI 一致性** | 扩展 UI 走 `<iframe src="/ext/<id>/index.html">` 或 vanilla JS 注入,跟现有风格一致 |
| **调试便利** | Node 子进程 `--inspect` + Chrome DevTools 直接断点 |

### 1.2 劣势

| 维度 | 评估 |
|------|------|
| **进程开销** | 每个扩展 1 个 node 子进程,启动 200-400ms,内存 30-50MB(扩展多了会胖) |
| **打包大小** | Node 二进制 + npm 依赖会拉 30-80MB(壳已经 360MB,不致命) |
| **沙箱粒度** | Node 没浏览器那种 origin 隔离,文件/网络权限要自管([[prisIrAI-perm-gate-v1]] 复用) |
| **TypeScript** | 后期扩展要 tsc 编译,工具链复杂度 +1 |

### 1.3 跟 PrisirAI 现有架构契合点(具体)

| 现有能力 | 扩展如何复用 |
|----------|-------------|
| `/prisiragent/api/*` HTTP 端点 | 扩展注册自己前缀的 endpoint,Python 端转发 |
| `_emit_event(sid, event)` | 扩展能 push 事件给主会话(LLM 流式输出、UI 卡片) |
| 权限闸弹卡 v1 | 扩展首次启用时复用同一 UI |
| 项目目录 `_PROJECTS` | 扩展可以声明「按项目启用」(例:Mermaid 扩展只在 web 项目启用) |
| 顶栏按钮(emoji + 文字) | 🧩 按钮直接复用 `.topbtn` 样式 |
| 抽屉/Modal CSS 变量(`--gh-paper` 等) | 扩展 UI 也能用,风格统一 |
| SQLite(`~/.prisir/chat.sqlite3`) | 扩展数据存 `~/.prisir/extensions/<id>/state.json`,不污染主库 |

---

## 2. Rust + Tauri 路线评估(备选)

### 2.1 优势

| 维度 | 评估 |
|------|------|
| **性能** | 内存占用 10MB 级 vs Electron 100MB+,启动 200ms vs 800ms |
| **原生扩展** | `.so`/`.dll` FFI,扩展能力直接到系统调用 |
| **单 binary** | Tauri 编译产物小,分发简单 |
| **Prisir 浏览器同源** | M3 Chromium 用 Rust,扩展机制能复用 chromium extension API |

### 2.2 劣势

| 维度 | 评估 |
|------|------|
| **vs PrisirAI 现状契合度** | ★★☆☆☆ — 当前 Python 单文件架构,Rust 路线要求重写 70% |
| **学习曲线** | Rust + tokio + Tauri + wasm,**招人/招协作者极难** |
| **UI 工具链** | Tauri 前端仍要 JS,但要绕开 Electron API,PrisirAI 现有的 vanilla JS 风格要重写一部分 |
| **增量交付差** | 「先做 1 个示例扩展证明价值」要 2-3 周(配 Rust toolchain + ABI 设计) |
| **vs Prisir 浏览器冲突** | 浏览器扩展是另一套规范(manifest v3),PrisirAI 扩展跟它不一样,容易认知混乱 |

### 2.3 Rust 路线真正的赢面

只在 1 个场景下值得:**PrisirAI 整体重写为 Tauri 壳 + Rust 后端**(用户问的「性能最好语言重写」路线)。但这是另一个决定,跟扩展系统正交。**先做 Node,等真要重写 PrisirAI 整体时再迁 Rust 扩展层**。

---

## 3. 路线决策:Node 优先,Rust 备而不用

### 3.1 阶段计划

#### **Phase 1 — 最小可用扩展(MVE,2-3 天)**
- [ ] Python 端:`_EXTENSIONS` 注册表 + `~/.prisir/extensions/installed.json` + `/prisiragent/api/extensions?op=list|install|enable|disable|uninstall`
- [ ] Python 端:进程管理器 `_ext_procs`,spawn Node 子进程 + stdio JSON-RPC
- [ ] 前端:🧩 顶栏按钮 + 右下角抽屉(列表态) + 安装流
- [ ] 示例扩展:`@prisir/ext-mermaid`(AI 检测 ```mermaid 代码块 → 抽屉外渲染 SVG 卡片)
- [ ] 权限闸复用:首次安装弹卡(改自 `prisIrAI-perm-card-ui-v1.md`)

#### **Phase 2 — 扩展 API v0.1(1 周)**
- [ ] `@prisir/extension-sdk`(npm,本地路径:`extensions/sdk/`):
  - `registerCommand(name, handler)` — AI 能调用
  - `registerPanel(id, html)` — 抽屉/主对话插卡片
  - `registerFileProvider(scheme, handler)` — `prisir-ext://xxx`
  - `onSessionStart/End/Message(cb)` — 钩子
- [ ] 文档:`docs/prisir-extension-api-v0.1.md`(API 参考 + 示例)

#### **Phase 3 — 商店(2 周)**
- [ ] GitHub 仓库 `PrisirAI/extensions`:`index.json` + tarball
- [ ] Python 端:`/api/extensions?op=browse` + 校验 SHA256 + 签名(初版 HMAC,后续换 sigstore)
- [ ] 前端:抽屉「浏览更多 →」入口
- [ ] 第三方扩展开发者指南:`docs/prisir-extension-author-guide.md`

#### **Phase 4 — 进阶(1 个月+)**
- [ ] 扩展按项目启用/禁用(`_PROJECTS` + `extensions.json` 关联)
- [ ] 扩展设置面板(各扩展暴露 `settings.json`,抽屉详情态编辑)
- [ ] 扩展更新检测(轮询 GitHub releases + semver 兼容)
- [ ] 扩展间通信(`extension.broadcast`)

#### **Phase 5 — 评估 Rust 迁移(待触发)**
- 触发条件:PrisirAI 整体重写决定 + Node 扩展数 > 20 个 + 性能瓶颈明确
- 此阶段才考虑把扩展层迁到 Tauri command,SDK 兼容层保证用户感知不变

### 3.2 不做 Rust 的明确理由

1. **当前架构没有性能瓶颈** — ThreadingHTTPServer 单进程 18802 端口,实测 200 并发无压力
2. **用户感知不到语言差异** — Node 跟 Rust 跑 LLM 调用,IO 都是异步,端到端延迟差 < 20ms
3. **招人/招协作者** — Rust 路线 5x 难度,Node 路线能借现有 VSCode 扩展作者社区
4. **失败成本** — Rust 写一半发现不对,迁移成本极高;Node 写一半可扔

---

## 4. 扩展 API v0.1 草案

### 4.1 进程模型

```
┌─────────────────────────────────────────────────────────────┐
│                    PrisirAI 主进程 (Python)                   │
│  ThreadingHTTPServer 18802                                   │
│  SQLite ~/.prisir/chat.sqlite3                               │
│  _EXTENSIONS 注册表 + _ext_procs 进程表                      │
└────────────────────────┬────────────────────────────────────┘
                         │ stdio JSON-RPC (NDJSON 1 行 1 消息)
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ Ext Mermaid  │ │ Ext SQL      │ │ Ext ...      │
│ node 子进程   │ │ node 子进程   │ │ node 子进程   │
│ @prisir/ext- │ │ @prisir/ext- │ │              │
│   mermaid    │ │   sql        │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
```

### 4.2 JSON-RPC 协议(stdio NDJSON)

**主 → 扩展**:
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"ext_id":"mermaid","session_id":"abc","workdir":"D:\\Projects\\foo"}}
{"jsonrpc":"2.0","method":"event","params":{"type":"session.message","session_id":"abc","role":"user","text":"..."}}
{"jsonrpc":"2.0","id":2,"method":"invoke","params":{"command":"render.mermaid","args":{"code":"graph TD;A-->B"}}}
```

**扩展 → 主**:
```json
{"jsonrpc":"2.0","id":1,"result":{"ok":true,"capabilities":["command:render.mermaid","panel:mermaid.preview"]}}
{"jsonrpc":"2.0","id":2,"result":{"html":"<svg>...</svg>","type":"card"}}
{"jsonrpc":"2.0","method":"ui.inject","params":{"session_id":"abc","target":"messages","html":"<svg>...</svg>","card_id":"m-42"}}
{"jsonrpc":"2.0","method":"log","params":{"level":"info","msg":"rendered svg 1.2KB"}}
```

### 4.3 SDK API(Node 端)

```typescript
// @prisir/extension-sdk
import { PrisIrExt, command, panel, fileProvider, hook } from '@prisir/extension-sdk';

const ext = new PrisIrExt({ id: 'mermaid', version: '0.3.0' });

// 1. 注册命令(AI 能调用)
ext.registerCommand('render.mermaid', async (args, ctx) => {
  const svg = await renderMermaid(args.code, args.theme);
  return { type: 'card', html: svg, meta: { engine: 'mermaid' } };
});

// 2. 注册面板(抽屉/主对话插卡片)
ext.registerPanel('mermaid.preview', {
  title: '设计稿预览',
  icon: '🎨',
  render: (state) => `<svg>${state.svg}</svg>`
});

// 3. 注册文件 provider(prisir-ext://xxx)
ext.registerFileProvider('mermaid', (path) => {
  // 返回 SVG/PNG/...
  return Buffer.from(svgBytes);
});

// 4. 钩子
ext.onSessionMessage(async (msg, ctx) => {
  if (msg.text.includes('```mermaid')) {
    ctx.injectCard({ /* ... */ });
  }
});

await ext.start();  // 启动 stdio JSON-RPC loop
```

### 4.4 主进程 API(HTTP)

| Endpoint | Method | Body | 用途 |
|----------|--------|------|------|
| `/api/extensions?op=list` | GET | - | 列出已装扩展 |
| `/api/extensions?op=install` | POST | `{url, sha256}` | 安装(暂只 GitHub tarball) |
| `/api/extensions?op=uninstall` | POST | `{ext_id}` | 卸载 |
| `/api/extensions?op=enable` | POST | `{ext_id}` | 启用 |
| `/api/extensions?op=disable` | POST | `{ext_id}` | 禁用 |
| `/api/extensions?op=config` | POST | `{ext_id, settings}` | 改设置 |
| `/api/extensions?op=invoke` | POST | `{ext_id, command, args, session_id}` | AI 调用扩展 |
| `/api/extensions?op=browse` | GET | `?q=&page=` | 浏览商店(读 GitHub index.json) |

---

## 5. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 扩展误用 AI 调用权限 | 权限闸强制:扩展注册命令时声明用途,首次 invoke 弹卡确认 |
| 扩展写坏主进程状态 | Node 子进程隔离,stdin 关闭即杀,主进程无共享内存 |
| 扩展阻塞主对话 | invoke 设 10s 超时,超时返回部分结果 + 提示 |
| 供应链(恶意扩展) | v0.1 只读 GitHub 策划列表 + HMAC 签名,后续接 sigstore |
| npm 依赖失控 | 扩展只能引 `@prisir/*` + 白名单 npm 包(`lodash`、`marked` 等 20 个),白名单外要求用户确认 |
| 扩展作者门槛 | 提供 `npx create-prisir-extension` 脚手架,5 分钟出 demo |

---

## 6. 何时回到用户拍板

| 决策点 | 触发条件 |
|--------|----------|
| 商店是 GitHub-only 还是开放任意 URL | Phase 3 启动前 |
| 扩展作者签名是必须还是可选 | Phase 3 启动前 |
| Rust 路线是否启动 | PrisirAI 整体重写决定(独立项目) |

---

## 7. 相关

- [[prisIr-extension-ui-mock-2026-09-20]] — B 形态详细界面草图(已交付)
- [[PrisirAI 权限闸 v1.0]] — 扩展权限弹卡复用基础
- [[PrisirAI 自学习闭环+estop]] — estop 机制可推广到「扩展 estop」(一键停所有扩展)
- [[oiagent 串行链状态]] — 跟 oiagent 派单系统的关系(扩展可注册命令给 oiagent 调用)
- [[PrisirAI 反馈落论坛]] — 扩展反馈走论坛 browser/shell 板块,跟产品反馈同一通道
- [[file-search-toolchain]] — 扩展可调 `prisir_findex` + `prisir_fcontent`,文件系统能力现成

---

## 8. 当前状态

- [x] 路线评估(Node 优先)
- [x] UI 形态(B 轻菜单型)
- [ ] 用户拍板「先做 Phase 1 MVE」vs「先观望」
- [ ] 拍板后开 Phase 1