---
name: gh-prisir
description: "PrisirAI 调 gh CLI 直接读 GitHub — repo/issue/PR/search 4 端点 + gh_api fetcher + gh_search provider,2026-09-26 ship P3j T21-C"
metadata:
  type: project
  originSessionId: 8770f801-490d-474f-9950-81a5d72bde7b
  modified: 2026-09-26T07:30:00.000Z
---

# PrisirAI × gh CLI 直接整合(P3j T21-C,2026-09-26 ship)

## 为什么做

Agent-Reach 上游工具有 gh CLI (`C:\Program Files\GitHub CLI\gh.exe`,
gh 2.96.0 + auth logged_in)。
**PrisirAI 之前没直接调 gh,GitHub 内容只能通过 jina/feedparser 抓 HTML
(经常风控 403)**。

## 集成方式(路径 C — 直接调上游 CLI)

| 能力 | PrisirAI 原(走 jina/HTML) | gh CLI 直接调 | 入口 |
|------|---------------------------|----------------|------|
| 仓库元数据(stars/forks/desc) | HTML 解析(易风控) | gh api 结构化 JSON | `web.gh.repo` + `gh_api_provider` |
| Issue / PR 读 | HTML 解析(标签散落) | gh api `repos/.../issues/N` | `web.gh.issue` + `gh_api_provider` |
| GitHub 搜索 | ❌ 没接 | gh search(repos/issues/prs/code) | `web.gh.search` + `gh_search` provider |
| auth 探测 | ❌ | gh auth status | `web.gh.health` |

## 模块/端点

- `prisir_work/gh_bridge.py` — ~210 行,5 公开 fn:
  `gh_health / repo_info / issue_get / pr_get / search`
- `prisir_work/gh_api_provider.py` — ~120 行,`gh_api_provider(url, options)` fetcher
  - github.com URL 路由:`/owner/repo` → repo_info;
    `/owner/repo/issues/N` 或 `/pull/N` → issue_get
- `prisir_work/endpoints.py:_web_gh_*` — 4 个 L0 端点
  (`/web/gh/{health,repo,issue,search}`)
- `prisir_work/capability.py:web.gh.{health,repo,issue,search}` — 4 个 capability
- `prisir_work/web_fetch.py` — 注册 gh_api fetcher(picker 兜底)
- `prisir_work/web_search.py` — 注册 gh_search provider(只在 `shutil.which("gh")` 命中时)

## 设计要点

- **gh --jq 过滤字段**:repo_info/issue_get/pr_get 走 `--jq` 只取必要字段
- **search --json 字段细分**:repos/issues/prs/code 支持的字段不同
  (gh 2.96 实测:repos 没 `title` 字段),按 kind 用不同字段白名单
- **失败一律不 raise**:subprocess.run 全部 try/except
- **auth 状态暴露**:`gh_health.auth_status = logged_in / not_logged_in`,
  给 UI 灯看(private repo 才需登录)
- **gh_search provider lazy 注册**:只在 `shutil.which("gh")` 命中时注册

## picker / search 优先级

```
github.com URL → jina(若未 403)→ gh_api 兜底(若 jina 失败)
搜索 query    → ddg/baidu/bing/jina_search/tavily/serper + gh_search
                  全部并发 → RRF rank fusion
```

## 与 agent-reach 的关系

- **agent-reach `read github`**:Playwright + 鉴权(支持 private 仓库)
- **gh_bridge**:gh api(public 仓库免 token)
- **互补不冲突**:public 走 gh,private 走 agent-reach

## Why

User asked「请将其上游工具逐一集成」。
盘点 14 项上游工具,gh CLI 是真 gap #3(系统 CLI 已装 + 已登录)。
**接进来**。

## How to apply

- 主对话问「GitHub 仓库 X 多少 stars」→ `web.gh.repo`
- 主对话问「GitHub issue #N 说什么」→ `web.gh.issue`
- 主对话问「GitHub 上搜 X」→ `web.gh.search` 或 web_search.merge 自动含 gh_search
- github.com URL → `web_fetch.fetch(url)` 自动选 gh_api(若 jina 失败)

## 易踩坑

1. **gh search `--json` 字段按 kind 不同** — 必须按 kind 分白名单,不然
   `Unknown JSON field: "title"` 挂
2. **gh auth 已登录但未测过**:`gh_health.auth_status` 给 UI 灯看
3. **gh API 60 req/h 匿名限流**:long-term 要 `gh auth login` 升 5000/h
4. **测试 mock `_run`**:monkeypatch `"prisir_work.gh_bridge._run"`
5. **web_fetch 触发 lazy register**:测试需要 try-fetch 一次让
   `_FETCHERS` 装上
6. **gh 不在 PATH**:gh_search provider 自动跳过注册(失败 soft);
   gh_health.installed=False
7. **GitHub 不识别 3+ 段 URL**:`/settings/profile/edit` → 
   `error=unsupported_github_url`

## 关键 commit

- T21-A feedparser ✅
- T21-B yt-dlp ✅
- T21-C gh CLI ✅(本批)
- T21-D 推迟项决策记录(下批)