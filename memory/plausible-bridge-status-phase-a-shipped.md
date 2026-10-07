---
name: plausible-bridge-status-phase-a-shipped
description: Plausible Analytics Phase A ship — bearer SDK 第 5 用户 + SDK 边界决策(GET 复用 + POST inline)
metadata:
  type: project
---

# Plausible Analytics Phase A ship — bearer SDK 第 5 用户(2026-10-07)

## 起因

继续 Phase A 模板跨域验证 — **Plausible 是 bearer-client.js SDK 第五个用户**,累计达 SDK 抽取持续价值阈值。

**SDK 边界决策**:Plausible Stats API v2 是 `POST /api/v2/query` + JSON body,**SDK 当前只 httpGet**。两个选择:
1. 扩展 bearer SDK 加 `httpPostJson()` —— SDK 膨胀风险(只为 Plausible 加 POST)
2. Plausible 扩展 inline 30 行 `httpPostJson` helper,GET 端点仍走 SDK

**选 (2)** —— 有意的 SDK 边界设计:**GET 通用 SDK 复用,POST 罕见 inline**。避免 SDK 为单扩展膨胀。

## 设计要点

### Plausible 扩展 `plausible-bridge-status/index.js`(170 行)

环境变量:
- `PRISIR_PLAUSIBLE_URL` 默认 `http://127.0.0.1:8000`(Plausible Docker 默认)
- `PRISIR_PLAUSIBLE_API_KEY` Bearer API key(Plausible User → API Keys)

3 个 L0 命令(纯只读):
- `plausible.health` — GET `/api/v1/sites`(SDK httpGet 复用,不消耗 stats quota)
- `plausible.sites` — GET `/api/v1/sites`(SDK httpGet 复用,返站点列表)
- `plausible.summary` — POST `/api/v2/query`(inline httpPostJson,返聚合 stats)

**绝不**触碰 POST `/api/v1/sites` 创建站点 / DELETE `/api/v1/sites/{domain}`。

### httpPostJson inline helper(30 行)

```js
function httpPostJson(args) {
  return new Promise((resolve) => {
    const token = cfg.token_();
    if (!token) return resolve({ ok: false, ..., error: 'no credentials' });
    const body = JSON.stringify(args.body || {});
    const headers = {
      'Authorization': `Bearer ${token}`,
      'Content-Type': 'application/json',
      'Content-Length': Buffer.byteLength(body),
    };
    const req = http.request(url, { method: 'POST', ..., headers }, ...);
    req.write(body);
    req.end();
  });
}
```

**与 SDK 失败语义对齐**:同样三层(缺 token / 网络错 / HTTP 4xx5xx),`r.status` + `r.error` 字段命名一致。

### fetchSummary 入参验证

`fetchSummary({site_id, metrics?, date_range?})`:
- 缺 `site_id` → 直接 `ok=false + last_error: 'missing required arg: site_id'`,不调 HTTP(节省 API quota + 用户体验)
- `metrics` 默认 `['visitors', 'pageviews', 'bounce_rate', 'visit_duration']`
- `date_range` 默认 `'7d'`

## 实现

### SDK 复用 60% / inline 40%

```
GET 端点(SDK httpGet):           health / sites   → 2/3 L0 命令
POST 端点(inline httpPostJson):   summary          → 1/3 L0 命令
```

**SDK 复用经济性继续验证** —— Plausible 是 SDK 第 5 用户,SDK 跨域进入「数据分析」领域。

### SDK 边界文档化

Plausible 扩展 docstring 明写 SDK 边界决策:
```
**SDK 边界决策**:Plausible Stats API v2 用 POST JSON body(非 GET),
SDK 当前只 httpGet。**Plausible 扩展 inline httpPostJson helper** 30 行,
sites/health 等 GET 端点仍走 SDK(httpGet)。这是有意的 SDK 边界:
  - httpGet: 通用,SDK 复用
  - httpPostJson: 罕见(POST + body),inline 避免 SDK 膨胀
```

下次做 Plausible 类似扩展(Grafana / Metabase / n8n / Superset)有 POST 需求时,可抄 inline 30 行 helper(无需抽 SDK)。

## 测试结果

**Plausible 单扩**:
```
node __tests__/run.js          → 7/7 ✓
node __tests__/run.js --e2e    → 11/11 ✓(7 单 + 4 E2E,覆盖 GET + POST + 401)
```

E2E mock server 验:
- Bearer token 接受 + 401 拒绝
- GET `/api/v1/sites` SDK httpGet 返 2 sites
- POST `/api/v2/query` inline httpPostJson 返聚合 metrics
- `site_id` 缺失检测

**Python wrapper**:
```
$ python -m pytest tests/test_plausible_bridge_status.py
============================== 4 passed in 1.04s ==============================
```

**14 扩展联合回归**:
```
======================== 54 passed, 2 skipped in 8.80s ========================
```

## Phase A 模板跨域矩阵(4 SDK,5 协议)

| SDK | 用户 | 模式 |
|---|---|---|
| subsonic-client.js | 3(Navidrome/Funkwhale/LMS) | 鉴权 salted token |
| **bearer-client.js** | **5(Audiobookshelf/Kavita/Gitea/Drone CI/Plausible)** | **RFC 6750 Bearer** |
| custom-auth-client.js | 4(Komga/Miniflux/Plex/Immich) | 自定义鉴权头 |
| SQLite 自写 | 2(SiYuan/Calibre) | node:sqlite readonly |
| anonymous 自写 | 2(LX/YesPlayMusic) | 局域 Open API |

**扩展家族跨域**:音乐 + 笔记 + 媒体中心 + 漫画 + RSS + 影视 + 照片 + 书库 + 代码托管 + CI/CD + **数据分析**(11 个领域)。

## SDK 抽取经济性(5 用户累计)

| 维度 | 抽 SDK 前 | 抽 SDK 后(5 用户) |
|---|---|---|
| 单扩展代码 | ~200 行 | 140-170 行 |
| Bearer helper | 35-50 行/inline/扩展 | SDK 复用 0 行 |
| SDK 总代码 | 0 | 114 行 |
| 跨域复用 | 0 | 5 用户(媒体 + CI/CD + 分析) |

**Plausible 累计经济性**:5 用户共享 114 行 SDK,平均每扩展省 ~40 行 = 5 × 40 = 200 行节省,SDK 投资 114 行 = **净赚 86 行 + 5 域跨域一致**。

## Why

Plausible 是 **bearer SDK 第五个用户**(累计),验证:

1. **SDK 跨域进入数据分析域**(媒体 → CI/CD → 分析),真正全栈跨域
2. **SDK 边界决策**(GET SDK + POST inline)防止 SDK 膨胀
3. **失败语义对齐** —— SDK 三层 + inline helper 三层一致,L0 命令无需区分

**SDK 抽取继续阈值**:5+ 用户(已达),继续 ship 同模式扩展(Nextcloud / Outline / GitLab OAuth)继续放大 SDK 经济性。

## How to apply

下次做 Pure Bearer 扩展(Nextcloud / Outline / GitLab OAuth / Forgejo / Woodpecker CI):

1. 端口确认 + token 来源
2. `require('../_scaffold/bearer-client')` —— **零修改**(GET 端点)
3. POST 端点(如有)→ inline 30 行 `httpPostJson` helper(Plausible 模板)
4. 写 L0 命令 + **入参验证 + 失败分支 http_status**(Kavita 教训)
5. 测三件套(Node 单测 + E2E mock server + Python wrapper)
6. 跑联合回归

工作量:**140-170 行 index.js**(取决于 POST 端点数量)。

下次做需要 SDK 增强: **若累计 2+ 扩展都需 POST + Bearer**(如 Nextcloud + Outline),考虑加 `httpPostJson()` 进 bearer SDK。当前单扩展 inline 不值得 SDK 膨胀。

相关:[[drone-bridge-status-phase-a-shipped]], [[gitea-bridge-status-phase-a-shipped]], [[audiobookshelf-bridge-status-phase-a-shipped]], [[kavita-bridge-status-phase-a-shipped]], [[extension-spec-and-manifest-schema-shipped]]
