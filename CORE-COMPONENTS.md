# Core Components (OIE-PCS-1.0 §1, §3)

This document is incorporated into the OI Enhancements Personal and
Commercial Source License (OIE-PCS-1.0). Modifications to the files and
directories listed below, when **Distributed** or made available as a
**Network Service** (as defined in LICENSE §1 and §3), must be made
available under the terms of OIE-PCS-1.0 per LICENSE §3.

This list is the **single source of truth** for which paths are
"Core Components". Paths NOT listed here are not Core Components and
are not subject to the source-availability obligation of LICENSE §3
when used outside Commercial Use.


## Core Components (modifications must be made available under OIE-PCS-1.0)

The following paths are Core Components. Modifications inside these
paths, when Distributed or made available as a Network Service, must
be made available under OIE-PCS-1.0.

### prisiragent runtime (Python 后端)
- `prisiragent_web.py`                      — 主 Web 后端(国画风聊天 UI + 路由 + LLM 代理)
- `prisiragent_cli.py`                      — CLI 入口
- `prisiragent_context.py`                  — 上下文用量统计
- `l4_web.py`                               — l4 工具/Web 后端(v0.5 起下沉到主仓)
- `policy_engine.py`                        — 策略引擎
- `demo_agent_loop.py`, `demo_gui_loop.py`  — 端到端演示回路

### prisiragent-shell (Electron 对话壳)
- `prisiragent-shell/main.js`                   — 主进程(后端 spawn + 窗口/托盘/全局热键)
- `prisiragent-shell/mini-main.js`              — 极简主进程(轻量打包入口)
- `prisiragent-shell/preload.js`                — 渲染层 preload(白名单 IPC)
- `prisiragent-shell/package.json`              — 壳依赖与打包配置
- `prisiragent-shell/make_shortcut.ps1`         — Windows 桌面快捷方式

### prisiragent router / shared memory / 端侧 Agent
- `dynamic_router/router.py`                — 动态路由
- `shared_memory/`                          — 进程间共享内存层
- `shared_memory_recompile/`                — shared_memory 重编译版
- `mcp_prisiragent_server/`                     — prisiragent MCP 服务端(供外部 Agent 调用)
- `subagent_depth/`                         — 子 Agent 深度治理

### agent_shell / 端侧对话壳
- `agent_shell/app.py`, `agent_shell/__main__.py` — agent_shell 入口
- `agent_shell/client.py`, `agent_shell/hooks.py`  — 客户端 + 钩子
- `agent_shell/asr_stream.py`, `agent_shell/ptt.py` — 语音流 + 按键说话
- `agent_shell/hotkeys.py`, `agent_shell/tray.py` — 全局热键 + 系统托盘
- `agent_shell/floating_orb.py`, `agent_shell/oi_pipeline.py` — 浮窗 + 流水线
- `agent_shell/scripts/run_agent_shell.bat` — Windows 启动脚本

### prisir_findex (Rust 本机文件搜索引擎)
- `prisir_findex/src/`                      — Rust 引擎源码
- `prisir_findex/Cargo.toml`
- `prisir_findex/Cargo.lock`
- `prisir_findex/shell_findex.py`           — Python ctypes 封装

### prisir_fcontent (Rust + Python 文件内容索引 / OCR / 翻译)
- `prisir_fcontent/__init__.py`
- `prisir_fcontent/engine.py`               — 索引引擎
- `prisir_fcontent/extract.py`              — 内容抽取
- `prisir_fcontent/ocr_eval.py`             — OCR 评估
- `prisir_fcontent/overlay_translate.py`    — 浮窗翻译
- `prisir_fcontent/tokenize.py`             — 分词
- `prisir_fcontent/verify.py`               — 校验
- `prisir_fcontent/models/`                 — OCR 模型权重目录(单独授权,见 THIRD-PARTY-NOTICES)

### Fastlane (LLM 路由层)
- `fastlane/adapters/`                      — LLM adapter(Anthropic / OpenAI / 兼容 API)
- `fastlane/adapters/main.py`               — adapter 入口
- `fastlane/providers/`                     — provider 工厂
- `fastlane/providers/factory.py`
- `fastlane/providers/llm_cloud.py`         — 云端 LLM provider
- `fastlane/llama_local_server.py`          — 本地 llama.cpp 服务
- `fastlane/setup_llama_server.ps1`         — Windows 本地服务安装

### Prisir 桌面 / 端侧 Agent 核心
- `aureon/`                                 — 端侧 Agent 核心模块
- `aureon/nix/`                             — Nix 表达式
- `prisir_asr/`                             — 端侧 ASR 客户端
- `prisir-browser/`                         — Prisir 浏览器侧
- `prisir_ime/`                             — 灵犀输入法(Rust FFI + Python 壳)
- `prisir_ime/src/`                         — Rust 引擎(jni / ffi / lib / engine)
- `prisir_work/`                            — PrisirWork(GitHub connector 多 session)
- `prisir_android/`                         — Prisir 安卓壳工程(资源 + 图标)
- `securedm-shell/`                         — SecureDM 对话壳
- `stagehand_oi/`                           — Stagehand 接入层
- `im_clients/`                             — IM 客户端集成

### 配对 / 局域网 / 遥控核心
- `e2e_share_a2h/`                          — agent ↔ host 共享/配对核心
- `e2e_share_rot/`                          — 旋转/路由
- `crypto_conduit/`                         — token / 加密通道核心
- `crypto_conduit/src/`                     — Rust 源码(identity / ratchet / session / wire)
- `lan_pair.py`                             — 局域网配对
- `chatroom_bot.py`, `chatroom_relay.py`, `chatroom_client.py` — chatroom 机器人/中继/客户端
- `securedm_web.py`, `securedm_groupchat_e2e.py` — SecureDM Web/群聊

### 视觉 / 感知 / 浏览器
- `vision/`                                 — 视觉模块
- `perception/`                             — 感知模块
- `browser_use_cli/`                        — 浏览器 CLI
- `cursor_harness_adapter/`                 — Cursor harness 适配
- `desktop/`                                — 桌面集成
- `a11y_extract/`                           — 无障碍抽取

### simplex / 加密 / 工具
- `simplex_auto_accept.py`, `simplex_integrity.py`
- `simplex_runtime.py`, `simplex_tools.py`, `simplex_totp.py`
- `stream_watchdog/`                        — 流监控

### 商业关键入口
- `ops_dashboard.py`                        — 运维仪表盘


## NOT Core Components (modifications do NOT trigger LICENSE §3 obligations)

The following are NOT Core Components. Modifications to these paths
do NOT, on their own, trigger the source-availability obligation of
LICENSE §3, provided such modifications are not Distributed as part
of Commercial Use without a commercial license.

### 文档
- `README.md`, `*.md` at any depth
- `chatroom.html`                           — 客户端页面 UI(纯前端)
- `CORE-COMPONENTS.md`, `LICENSE`, `LICENSE-APACHE`, `LICENSE-POLICY.md`
- `TRADEMARKS.md`, `COMMERCIAL-LICENSE.md`, `CONTRIBUTING.md`, `SECURITY.md`
- `THIRD-PARTY-NOTICES`

### 测试 / 评估 / 临时演示
- `tests/`, `test/`, `*_test/`, `*_eval/`
- `audio_voice_eval/`
- `e2e/`, `e2e_*`(除非上面已列为 Core)
- `test_*.py` 在仓库根
- `.tmp_*`, `_*.md`, `_*.py`, `_*.png`, `_*.log`, `_*.db`

### 第三方依赖与运行产物
- `node_modules/`
- `prisir_findex/target/`                   — Rust 编译产物
- `prisir_fcontent/models/` 已在 Core 列出(模型权重单独授权)
- `dist/`, `build/`, `__pycache__/`, `.venv/`
- `vendor/`, `vendor/**`                    — 第三方 vendored 代码(单独授权)

### 个人开发 / 实验 / 备份 / 临时
- `_*.py`, `_*.png`, `_*.log`, `_*.db`
- `backup-*/`
- `%TEMP%/`
- `*.bak`, `*.tmp`
- `memory/`                                 — 个人记忆(本机生成)

### 用户级配置 / 运行时数据库(本机生成,不进入仓库)
- `prisir_findex/findex.db`, `findex.db-shm`, `findex.db-wal`
- `prisir_fcontent/fcontent.db`, `fcontent.db-shm`, `fcontent.db-wal`

### 构建与安装脚本(可独立选择许可证使用)
- `installer/`                              — 安装/卸载脚本(可作为独立产物使用)
- `installer/PrisirAI.vbs`, `installer/launcher.bat` — Win-side 启动器
- `installer/linux-install.sh`, `installer/linux-uninstall.sh` — Linux 装包
- `installer/build_repo_zip.py`             — repo.zip 打包器
- `fastlane/setup_llama_server.ps1`         — 本地 llama 服务安装脚本(非路由核心)

### 资源与第三方资源
- `assets/`                                 — 图标/UI 资源;Brand 元素使用受 TRADEMARKS.md 约束
- `audio/`, `audio_voice_eval/`             — 语音/TTS 样本


## How to interpret this list

- Paths are matched as **prefixes** (directory) or **exact files**.
- A modification that **transitively** affects a Core Component
  (e.g. by changing its public API used by another module) is
  itself considered a modification of the Core Component for the
  purposes of LICENSE §3.
- If a Core Component path is renamed or moved, this document is
  authoritative: the path listed here continues to be a Core Component
  regardless of the actual file system location, and the contributor
  of the rename must update this document in the same commit.
- If You are uncertain whether a path is a Core Component, treat it
  as a Core Component, or contact the Project Copyright Holder before
  Distribution.


## Changes to this document

This document may be amended by the Project Copyright Holder at any
time. The version of this document in effect at the time of Your
Distribution governs the source-availability obligation for that
Distribution.

Last updated: 2026-08-28
