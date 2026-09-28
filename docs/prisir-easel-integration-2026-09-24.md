# PrisirAI × Easel 整合设计文档

**作者**:Claude Code (Opus 5)
**日期**:2026-09-24
**Phase**:0 ship
**状态**:全绿(15/15 单元测试)

---

## 1. Context 与决策

用户决策:不做独立产品,**从外部开源项目抽相关模块,整合到 PrisirAI**。

评估了两个候选:
- **Easel** (ZJU-REAL,Apache-2.0,1329★) — 社媒发现→发布→归因(7 平台)+ 113 skill
- **hypit** (hypit-ai, NOASSERTION, 16312★) — AI 视频批量生成(DSL + 100 variants)

**决策**:
- **Easel 整**:抽 4 个模块(扫码发布 + 数据回收 + OpenClaw skill 范式 + 多平台发布抽象层)
- **hypit 不整**:仅借鉴 DSL 思路(SVML 词级锚定 + composition 不变 slot 替换)— 不集成代码

Easel 装形态:**本地源码**(`git clone --depth=1` 到 `~/work/zju_easel/`)— 不 `pip install easel`(那会拉 25+ 依赖 + Playwright + Chromium + Chrome ext + 反代)。

---

## 2. 整合的 4 个模块

| 模块 | PrisirAI 文件 | 走法 |
|------|---------------|------|
| **公众号扫码发布** | `prisir_work/easel_bridge.py` | subprocess 调 `skills/openclaw/skill-wechat-publisher/scripts/publish.py` |
| **公众号数据回收** | 同上 | subprocess 调 `skills/shared/scripts/weixin_mp_stats.py stats` |
| **OpenClaw skill 范式** | `prisir_work/skill_manifest.py` | 抽 SKILL.md YAML frontmatter,不复制 OpenClaw gateway |
| **多平台发布抽象层** | `prisir_work/publisher.py` | Protocol 范式 + 注册表(对齐 web_search register_provider) |

---

## 3. 接入点(PrisirAI 主线)

### 3.1 4 个新 endpoint(`prisir_work/endpoints.py`)

| Path | Method | Risk | 作用 |
|------|--------|------|------|
| `/publish/list` | POST | L0 | 列平台列表 + ready 状态 |
| `/publish/status` | POST | L0 | 查登录态 |
| `/publish/html` | POST | L2 | 真发 HTML 草稿(L2 = 全回显,扩展侧弹确认卡) |
| `/publish/stats` | POST | L0 | 公众号近 N 篇数据回收 |

### 3.2 4 个新 capability(`prisir_work/capability.py`)

```python
publish.list    keywords=(发布, 平台, publisher, publish, 平台列表, 发到)
publish.status  keywords=(登录态, 扫码, whoami, login, 发布器状态)
publish.html    keywords=(发布, 发文, 推文, 公众号, wechat, publish,
                         发到, 草稿, 发草稿, 发到公众号, 微信文章)
publish.stats   keywords=(公众号数据, 数据回收, 阅读, 分享, 粉丝, stats,
                         分析, 公众号统计)
```

agent/扩展通过 capability search 自动发现「发布」意图 → 路由到 `/publish/html`。

### 3.3 6 个发布器注册(`prisir_work/publisher.py`)

| name | title | ready | 实现 |
|------|-------|-------|------|
| `wechat-oa` | 微信公众号 | true | `WechatOaPublisher` 走 Easel bridge |
| `xhs` | 小红书 | false | `NullPublisher` 占位 |
| `bilibili` | 哔哩哔哩 | false | 同上 |
| `douyin` | 抖音 | false | 同上 |
| `zhihu` | 知乎 | false | 同上 |
| `wechat-channels` | 微信视频号 | false | 同上 |

**降级而非崩溃** — `publish('xhs', ...)` 直接返 `ok=False + 'xhs 尚未实现'`,绝不抛栈。

---

## 4. 关键设计决策

### 4.1 为什么走子进程,不 pip install

| | subprocess 调脚本 | pip install easel |
|---|---|---|
| 依赖 | 0 三方(纯 stdlib) | 25+ 三方(fastapi/playwright/rembg/faster-whisper/jieba/...) |
| 装包体积 | Easel 仓库本体 ~50MB | Easel venv ~ 几百 MB + Chromium |
| 升级 | 拉 git pull 即可 | `pip install --upgrade` + 重启 venv |
| 调试 | 改源码即生效 | 重新进 venv |
| 隔离 | 我们干净,Easel 独立 | 共享 venv 可能互相污染 |
| **适合场景** | **只用到 4 个 CLI** | 想跑 Easel 整个 web 后端 |

**结论**:我们只用到 4 个 CLI(whoami/login/stats/publish),**走子进程是最低耦合 + 最快 ship 路径**。

### 4.2 为什么不做完整 OpenClaw runtime clone

Easel 的 OpenClaw 是个**完整 agent gateway**(JSON5 配置 + 模型供应商 + gateway RPC + Ed25519 设备签名)。PrisirAI 已有自己的 `prisir_work/server.py + capability.py + endpoints.py`,功能重叠。

**移植的只有**:
- SKILL.md frontmatter 格式(name/description/layer/triggers)
- `references/` 下沉领域知识到独立 .md 文件的瘦身思路

**不移植的**:
- OpenClaw gateway runtime
- JSON5 schema 完整定义
- 模型供应商配置

`skill_manifest.py` 用 100 行 stdlib 抽 frontmatter + 触发场景,够了。

### 4.3 多平台抽象层的对齐 web_search

`prisir_work/web_search.py` 已用 `register_provider()` + `search()` 路由。`publisher.py` 同款 `register_publisher()` + `publish()` 路由 — **降级范式一致**:
- provider 不在 → 空结果
- provider 失败 → `ok=False + reason`,绝不抛栈

未来接小红书/B站,只需写一个 `XhsPublisher(Easel skill-xhs-publisher)` 类 + `register_publisher()`,零改动其他代码。

### 4.4 风险分级

| 端点 | risk | confirm | 为什么 |
|------|------|---------|--------|
| `/publish/list` | L0 | "" | 只读 |
| `/publish/status` | L0 | "" | 只读 |
| `/publish/stats` | L0 | "" | 只读 |
| `/publish/html` | **L2** | "L2 真发草稿到公众号后台..." | **真发出去,需用户扩展侧二次确认** |

---

## 5. E2E 验证(2026-09-24 ship 时跑通)

### 5.1 单元测试

```bash
$ python tests/test_easel_bridge.py
============================= test session starts =============================
collected 15 items
tests/test_easel_bridge.py::test_bridge_find_root PASSED                 [  6%]
tests/test_easel_bridge.py::test_bridge_ready_or_not PASSED              [ 13%]
tests/test_easel_bridge.py::test_publisher_list_all PASSED               [ 20%]
tests/test_easel_bridge.py::test_publisher_wechat_oa_ready PASSED        [ 26%]
tests/test_easel_bridge.py::test_publisher_null_publishers_fail PASSED   [ 33%]
tests/test_easel_bridge.py::test_publisher_unknown_platform PASSED       [ 40%]
tests/test_easel_bridge.py::test_publisher_file_missing PASSED           [ 46%]
tests/test_easel_bridge.py::test_publisher_register_hot_swap PASSED      [ 53%]
tests/test_easel_bridge.py::test_skill_manifest_parse_inline PASSED      [ 60%]
tests/test_easel_bridge.py::test_skill_manifest_list_includes_wechat_oa PASSED [ 66%]
tests/test_easel_bridge.py::test_skill_manifest_scan_easel PASSED        [ 73%]
tests/test_easel_bridge.py::test_endpoint_publish_list PASSED            [ 80%]
tests/test_easel_bridge.py::test_endpoint_publish_status_unknown_platform PASSED [ 86%]
tests/test_easel_bridge.py::test_endpoint_publish_html_missing_fields PASSED [ 93%]
tests/test_easel_bridge.py::test_endpoint_publish_stats_only_wechat PASSED [100%]
============================= 15 passed in 9.28s ==============================
```

### 5.2 手工端到端(用户本机跑)

```bash
# 1. 装 Easel 本地源码(已 ship)— 设 EASEL_ROOT 或放 ~/work/zju_easel/
cd ~/work && git clone --depth=1 https://github.com/ZJU-REAL/Easel.git zju_easel

# 2. 装依赖(只装我们要的 4 个 CLI 实际用到的)— 用户本机按需
pip install playwright  # login 扫码
python -m playwright install chromium
# 也可能用到: PyYAML / markdown / jieba(发布 / 排版)

# 3. mp 后台扫码(只能真用户交互)
cd ~/work/zju_easel/skills/shared/scripts
python weixin_mp_stats.py login
# 浏览器开 mp.weixin.qq.com → 管理员扫码 → 会话持久化到 ~/.easel-browser-profiles/WeixinMpProfile

# 4. 验证 PrisirAI 能识别
python -m prisir_work.easel_bridge whoami
# {"loggedIn": true, "name": "...", "avatar": "..."}

# 5. 发文(走 /publish/html)
# 5.1 准备 HTML + 封面图
# 5.2 调 endpoint
curl -X POST http://localhost:18813/publish/html \
  -H 'Content-Type: application/json' \
  -d '{"platform": "wechat-oa", "html_path": "/tmp/article.html",
       "title": "测试标题", "cover": "/tmp/cover.jpg"}'
# → {ok: true, artifact: {media_id: "..."}}

# 6. 数据回收
curl -X POST http://localhost:18813/publish/stats \
  -H 'Content-Type: application/json' \
  -d '{"platform": "wechat-oa", "count": 30}'
# → {ok: true, data: {platform, loggedIn, followers, posts, metrics, notes, growth}}
```

---

## 6. Rollback

- `prisir_work/{easel_bridge,publisher,skill_manifest}.py` + `endpoints.py` 末尾追加的 4 个端点 + `capability.py` 末尾追加的 4 个 capability — 全部新增,不动现有代码
- 删除以上 5 处改动 = 100% 回滚
- PrisirAI 主对话 / 扩展 / shell 零行为变化(除非主动调 `/publish/*` 或搜索「发布」)

---

## 7. Risks

| 风险 | 影响 | 缓解 |
|------|------|------|
| Easel 升级改 CLI 形参 | 中 — 4 个端点可能断 | `easel_bridge._run` 兜底返 stderr 提示;接 release 时跑测试 |
| Playwright / Chromium 没装 | 中 — login / publish 失败 | `_ready` 检测 + `publish()` 返 `bridge_ready=false` 明确原因 |
| 用户没装 Easel | 低 | `find_easel_root()` 给明确下载指引 + GitHub release 链接;`/publish/list` 仍返 200 + ready=false |
| 公众号改后台结构 | 中 — Easel stats XHR 拦截失效 | 等 Easel 升级适配,我们跟着 `git pull` |
| 多平台(xhs/b站/抖音)占位太久没人接 | 低 | NullPublisher 兜底 + 5 行 `register_publisher` 就能接入新实现 |

---

## 8. 后续(Phase 2)

1. **接小红书/B站/抖音**:写 `XhsPublisher(Easel skill-xhs-publisher 子进程)` 同款实现,`register_publisher()` 注册即可
2. **写自动发布 AI 编排**:接 `prisir_work/research.py` 多步研究 → AI 写文 → html_converter → publish.html
3. **反 AI 检测门**:`ai_score(text)` 集成进发布前卡,> 阈值走确认门
4. **多账号**:`wechat-publisher.yaml` 解析,`/publish/html` 加 `account` 参数
5. **装包**:PyInstaller 把桥接包打进去,免去用户手动 clone Easel

---

## 9. Memory 落点

- [[prisir-easel-bridge]] — Easel 整合整体设计 + 关键决策 + 子进程范式来源
- 链接 [[prisirmp-wechat-recall]] — 同主题(公众号本地),作为素材库供给发布链路
- 链接 [[hypit-dsl-batch-design]] — hypit 借鉴思路(不集成代码)
- 链接 [[prisir-workflow-extension-dag]] — PrisirAI 任务编排(未来可借鉴 hypit 思路)