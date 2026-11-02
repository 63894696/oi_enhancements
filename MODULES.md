# MODULES.md — 模块边界登记（single source of truth）

> 生成：2026-11-02 ｜ 依据：全仓盘点（67 个一级目录 → 分类）
> 规则：新增目录必须先在本文登记再动代码；状态有变就地更新本表。
> 完整分析见 `D:\Projects\Prisir\docs\01-architecture\2026-11-02-项目区分分析.md`

## 状态定义

| 状态 | 含义 |
|---|---|
| `active` | 一等模块，改动不受限 |
| `active-main` | 主线模块，重大改动需先看边界 |
| `frozen` | 契约冻结，只修 bug 不演进（如 policy_engine） |
| `archived` | 已归档，只读参考 |
| `junk` | 应清理（已在 2026-11-02 清理完，若再出现即删） |

## A. Agent 内核

| 目录/文件 | 线 | 状态 | 备注 |
|---|---|---|---|
| `prisIragent_web.py` | 内核 | active-main | 15,623 行单体，2026-11 计划拆分到 `prisir_server/` |
| `prisIragent_cli.py` | 内核 | active-main | 4,206 行，随 web 一起拆 |
| `policy_engine.py` | 内核 | **frozen** | 13 用例全过，权限闸契约 |
| `prisir_case_compat.py` | 内核 | frozen | 大小写兼容层（双 IME 合并前不许动） |
| `mcp_prisiragent_server/` | 内核 | active | MCP 暴露层 v1（companion 引用，暂留） |
| `mcp_prisiragent_v3_plan_parallel/` | 内核 | active-main | MCP 主线（计划并行） |
| `fastlane/` | 内核 | active | LLM 路由 + 本地 llama |
| `dynamic_router/` `perception/` `vision/` `a11y_extract/` `browser_use_cli/` `desktop/` `stagehand_oi/` `cursor_harness_adapter/` `steward/` `stream_watchdog/` `subagent_depth/` `skills/` `agent_economy/` `shared_memory/` `shared_memory_recompile/` | 内核 | active | 小模块，合并/归档见分析文档 |

## B. 产品线

| 目录/文件 | 线 | 状态 | 独立发布计划 |
|---|---|---|---|
| `prisiragent-browser/` | b1 浏览器 | active | 随浏览器线（P2） |
| `prisiragent-shell/` | b1 浏览器 | active | Electron 壳 |
| `securedm_web.py` + `securedm-shell/` + `securedm-extension/` + `im_clients/` + `chatroom_*.py` + `simplex_*.py` + `simplex_files_root/` | b2 IM | active | **RIVO，第一个独立仓候选** |
| `crypto_conduit/` | b2 IM | frozen | Rust 加密底座，独立仓时先行 |
| `e2e_share_a2h/` `e2e_share_rot/` | b2 IM | active | 配对/遥控 |
| `prisir_ime/` | b3 输入法 | active | **引擎 crate**（Rust cdylib + 词库），三端共用 |
| `prisIragent_ime/` | b3 输入法 | active | **Windows TSF 壳 workspace**（3 crate）。与 `prisir_ime` 是同一输入法的两半；cargo 级合并待 Linux 构建机验证后做，合并前两目录边界不许再模糊 |
| `fcitx5-prisir/` | b3 输入法 | active | Linux Fcitx5 addon（已出 42MB deb） |
| `prisir_findex/` `prisir_fcontent/` | b4 检索 | active | Rust 引擎 + 内容索引 |
| `prisir_android/` | b5 移动 | active | Capacitor 壳 |
| `prisir_asr/` `voice_input/` | b6 语音 | active | SenseVoice + VAD |
| `prisIr_calendar/` + `prisIragent_calendar/` + `prisIr_calendar_data/` | b7 日历 | active | 后/前/数据三体，import 路径敏感，合并前先改引用（`prisIragent_web.py`、`prisiragent_coworker/permissions/`） |
| `agent_shell/` `companion/` `custom-hover-translate/` `prisir-video-study/` | b8 桌面 | active | companion 316 文件偏大，独立盘点 |
| `prisir_work/` | b9 门面 | active | wallet + web_auto + bridges |
| `aureon/` `wasm_meter/` | b10 端侧 | active | 联邦 + WASM 运行时 |
| `prisiragent_coworker/` `prisirmp/` `agentmail_helper/` `projects/` | 内核扩展 | active | cowork/多媒体/邮件/项目 |

## C. 扩展生态

| 目录 | 状态 | 备注 |
|---|---|---|
| `extensions/` | active | bearer SDK 系列（独立运营） |
| `ext-src/` | active | 扩展源说明 |

## D. 支撑

| 目录 | 状态 | 备注 |
|---|---|---|
| `installer/` | active | NSIS + Linux 装包（含 310MB 安装包与 dll 685 个） |
| `assets/` | active | 品牌资源（TRADEMARKS 约束） |
| `docs/` | active | 70 个文档 |
| `tests/` | active | 231 个测试 |
| `memory/` | active | 本机生成记忆（不入 git 的按 .gitignore） |
| `vendor/` `scripts/` `generated/` `audio_voice_eval/` | active | 第三方/脚本/评估样本 |
| `archive/2026-09/` | archived | `mcp_prisiragent_v2_self_fix`（自修复实验）、`prisiragent-tauri`（已放弃的壳） |

## 已知重复与合并计划（不重复造轮子清单）

| 重复项 | 状态 | 何时合并 | 阻塞点 |
|---|---|---|---|
| 双 IME（`prisir_ime` / `prisIragent_ime`） | 边界已划，未合并 | Linux 构建机可验证 cargo build 时 | `prisir_case_compat.py` 路径引用 |
| 三日历 | 未合并 | 改完 3 处引用后 | Python import 路径 |
| 双 shared_memory | 未合并 | 空闲时 | 无 |
| 双 MCP（v1/v3） | v2 已归档，v1 保留 | v3 完全接管后 | companion 引用 v1 |

## 已清理（2026-11-02，junk 类，gitignore 已覆盖）

`%TEMP%`、`_comet_probe`（2943 文件）、`_secb_profile`、`_secbrowser_profile`、`_vc2_profile`、
`_cleanup_2026-11-01`、`_stage_ime`、`_agnes_icons`、`_prisir_registry`、
`backup-a2-20260804-133318-bob`（含私钥，物理删除）、`logs`、`window-list`、`test_ignore_dir`、
`generated`、畸形空目录。**若这些名字再次出现，直接删，不登记。**
