---
name: agent-browser-prisir
description: P3j T26 vercel-labs/agent-browser 接入(token 高效浏览器,Rust CLI 子进程桥)
metadata:
  type: project
---

# P3j T26 — vercel-labs/agent-browser × PrisirAI(2026-09-26 ship)

**Why**:补 T25 Playwright MCP token 高消耗 + ref 不稳定的痛点;~93% token 削减
(@e1/@e2 refs 跨 snapshot 稳定),Native Rust 二进制(预编译,不需 Rust 工具链)。

**How to apply**:多步交互 / 表单填写 / 跨页跳转默认走 `web.agent-browser.*`;
长连实时自拼步骤仍用 T25 `web.playwright.*`;静态抓取走 web_fetch / jina / gh。

**核心模块**:
- [prisir_work/agent_browser_bridge.py](../prisir_work/agent_browser_bridge.py) — 8 公开 API(复用 `gh_bridge._run` 模式)
- [prisir_work/endpoints.py](../prisir_work/endpoints.py) — 8 endpoint(`/web/agent-browser/*`)
- [prisir_work/capability.py](../prisir_work/capability.py) — 8 capability(`web.agent-browser.*`)
- [prisir_work/agent_main_chat_hook.py](../prisir_work/agent_main_chat_hook.py) — 6 错误翻译(`ab_*`)
- [tests/test_agent_browser_bridge.py](../tests/test_agent_browser_bridge.py) — 18 mock case
- [tests/test_agent_browser_endpoints.py](../tests/test_agent_browser_endpoints.py) — 16 mock case
- [docs/agent-browser-prisir-2026-09-26.md](../docs/agent-browser-prisir-2026-09-26.md) — 设计 + E2E

**关键边界(决策已固化)**:
| 用例 | 推荐 |
|------|------|
| 多步交互 + 稳定 refs + token 敏感 | **agent-browser(T26)** |
| 长连会话 / 实时 LLM 自拼步骤 | playwright-mcp(T25) |
| 静态 HTML 抓 | web_fetch(urllib) |
| 防反爬 / JS 渲染抓 | web_fetch_jina |
| GitHub 元数据 | gh_bridge |
| RSS 抓 | feedparser |
| YouTube 字幕 | yt-dlp |

**Why**:T25 跟 T26 不是替代关系,而是「token 高效 vs 长连」互补:
- T25 stdio JSON-RPC 长连(Node.js MCP server,Chromium,~200MB,每次 snapshot 全树返)
- T26 batch subprocess CLI(Rust daemon,Chrome for Testing,~150MB,refs 跨 snapshot 稳定)

**坑固化**:
1. **refs 校验放最前** — `re.match(r"^@e\d+$", ref)` 不通过直接返 `bad_ref`,
   不进 subprocess(节省一次进程 + Rust IPC)。
2. **stderr 区分 invalid_ref vs 其他失败** — stderr 含 "ref" / "not found"
   → `ab_invalid_ref`,否则 `ab_failed`(LLM 才能区分该 snapshot vs 真错)。
3. **close 失败也返 ok=True** — daemon 可能已死,下次 call 自动重启,close
   报错不该 propagate。
4. **`--json` 由 bridge 内部追加**,不是 caller 责任。close 类命令不需要,
   调 `_run(args, need_json=False)` 即可。
5. **`subprocess.run` 必带 `encoding="utf-8", errors="replace"`** — stderr 可能
   含非 UTF-8 字节(Windows 上尤其)。

**何时重新评估**:
- agent-browser 出稳定 `batch` API → T26+1 加 `ab_batch(commands)` 跨步省 token
- 多模态成熟 → ab_screenshot 转 vision path(接 [[agent-screenshot]])
- Chrome for Testing 改 chromium-only → 调整 `_INSTALL_HINT`

**下一步(P3)**:
- T27: browser-use 接入(整 LLM 浏览器 agent,~115k stars)
- T28: agent-screenshot / vision-skills 接入(截图 + 视觉理解,接 T26 screenshot)

**关联**:[[playwright-mcp-prisir]] T25,[[gh-cli-prisir]] 复用 _run 模式,
[[tav-shell-port-pinning-regression]] dev 端 E2E 模式参考。