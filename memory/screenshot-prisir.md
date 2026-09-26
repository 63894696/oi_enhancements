---
name: screenshot-prisir
description: P3j T28 joeblack-lha/screenshot-mcp 接入(桌面截图,补完浏览器外场景)
metadata:
  type: project
---

# P3j T28 — joeblack-lha/screenshot-mcp × PrisirAI(2026-09-26 ship)

**Why**:补 T25 Playwright MCP / T26 agent-browser 只能截「浏览器渲染的页面」的盲点。
用户真实场景里大量需要截**整个桌面 / 当前窗口 / 自定义区域**(微信/钉钉/游戏/IDE 等),
T28 输出 PNG 自动进 vision 链路(若 LLM 多模态开),实现「LLM 看图」。

**How to apply**:
- 截浏览器渲染页面 → T25/T26(web.playwright.* / web.agent-browser.*)
- **截桌面 / 窗口 / 区域 → T28(web.screenshot.*)**
- 截屏后 LLM vision 看图 → T28 输出 PNG 自动进 vision 链路

**核心模块**:
- [prisir_work/screenshot_bridge.py](../prisir_work/screenshot_bridge.py) — 6 公开 API(复用 `gh_bridge._run` 模式)
- [prisir_work/endpoints.py](../prisir_work/endpoints.py) — 6 endpoint(`/web/screenshot/*`,全部 L0)
- [prisir_work/capability.py](../prisir_work/capability.py) — 6 capability(`web.screenshot.*`,全部 L0)
- [prisir_work/agent_main_chat_hook.py](../prisir_work/agent_main_chat_hook.py) — 9 错误翻译(`ss_*`)
- [tests/test_screenshot_bridge.py](../tests/test_screenshot_bridge.py) — 21 mock case
- [tests/test_screenshot_endpoints.py](../tests/test_screenshot_endpoints.py) — 14 mock case
- [docs/screenshot-prisir-2026-09-26.md](../docs/screenshot-prisir-2026-09-26.md) — 设计 + E2E

**关键边界(决策已固化)**:

| 用例 | 推荐 |
|------|------|
| 截浏览器渲染页面 | T25/T26(playwright/agent-browser) |
| **截整个桌面 / 窗口 / 区域** | **T28(screenshot-mcp)** |
| **截微信/钉钉/游戏/IDE 等桌面应用** | **T28** |
| **截屏后 LLM vision 看图** | **T28**(输出 PNG 进 vision 链路) |
| 静态抓取 / 防反爬 | web_fetch / jina / feedparser |

**为什么 T28 跟 T25/T26 不重叠**:
- T25 stdio JSON-RPC 长连(Node.js MCP server,Chromium,~200MB)
- T26 batch subprocess CLI(Rust daemon,Chrome for Testing,~150MB)
- **T28 batch subprocess CLI(无 daemon,无浏览器,调系统 API,~5MB npm)**—
  补「浏览器外」+「超轻量」两个轴

**坑固化**:
1. **4 档 mode 顺序必严格** — `missing_cli → missing_node → no_backend → ready`,
   任何一档不通过就返对应 mode,不让「看似 ready 实际截图失败」。
2. **area 格式校验放最前** — `x,y,w,h` 全部非负整数、宽高 > 0;不通过直接返
   `ss_invalid_area`,不进 subprocess(节省 100ms + 避免 PowerShell 抛错)。
3. **filename 防穿越** — 不允许 `/` `\` `..`,即便 bridge 直接拼 subprocess
   args(用户传 `../../etc/passwd.png` 也不该穿透到 system shell 路径)。
4. **`Saved to: <path>` 正则抽** — `r"Saved\s+to[:\s]+(\S+)"`,覆盖 `Saved to:`
   / `Saved   to:` / `Savedto:` 三种变体。
5. **`ss_active_backend` 纯本地** — 不调 subprocess,只 `shutil.which`,后端
   失败提示用户装(避免每次都跑 Node + screenshot-mcp)。
6. **PNG 尺寸读 8 字节** — 跳 PNG sig 8 字节 + IHDR 头 4 字节,直接 unpack 4 字节 width +
   4 字节 height,无需 PIL/Pillow 依赖(轻量 + 跨平台)。
7. **endpoint handler 不强覆盖 r["ok"]** — `return r, 200` 而非 `{"ok": True, **r}`,
   让底层 `ok=False` 透传到前端,前端能区分成功/失败。

**何时重新评估**:
- screenshot-mcp 出 v2(MCP server + CLI 双形态)→ 评估接 stdio JSON-RPC
- 多模态 LLM(vision)普及 → 加 `web.screenshot.recognize_text`(OCR 抽文本)
- 用户反复截大图 → 加自动压缩 + 缩略图
- macOS screencapture 命令改 → 重新适配

**关联**:[[agent-browser-prisir]] T26 / [[playwright-mcp-prisir]] T25,
[[gh-cli-prisir]] 复用 _run 模式,[[agent-tool-survey]] 8 大类决策表。