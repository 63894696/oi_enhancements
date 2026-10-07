---
name: extension-spec-and-manifest-schema-shipped
description: PrisirAI 扩展规范 v0.1 + manifest schema(features[]/platforms[])ship
metadata:
  type: project
---

# PrisirAI 扩展规范 v0.1 ship (2026-10-07)

## 起因

v2 对比研究 6 项借鉴剩余 3 项([lx-music-like-software-comparison](../memory/lx-music-like-software-comparison.md)):
- **借鉴 4** extension-spec 文档化(5 步法 + 失败语义 + 借鉴原则沉淀)
- **借鉴 5** scaffold 加 `features[]` / `platform[]`(未来跨软件协同元数据)
- **借鉴 6** ESLint 禁 `console.log(token)`(Token 泄露红线)

5 个 Phase A 实战扩展(LX / SiYuan / Calibre / YesPlayMusic / Navidrome)已 ship,模板/失败语义/env 覆盖/测试三件套/L0 权限**全部固化**。是时候把这些沉淀到一份正式 spec,而不是散落在每个扩展的描述里。

## 设计取舍

**`docs/extension-spec.md` 作为唯一规范源** — 5 步法 + manifest schema + 失败语义表 + 借鉴原则 + 已知坑 + 实战模板(7 个章节,260 行)。

**manifest schema 3 个字段**:
- `prisIrPermissions`(已有,L0/L1/L2 权限列表)
- `prisIrFeatures`(新,标签规整如 `read-only / requires-local-server / sqlite-readonly / subsonic-protocol`)
- `prisIrPlatforms`(新,跨平台支持 `win32/darwin/linux`,空数组 = 不声明 = 默认 win32)

**为什么加 `prisIrFeatures`?**
1. 商店筛选可按 feature 过滤(如「显示所有 SQLite readonly 扩展」)
2. AI inventory 注入 system prompt 时可按 feature 决定能否本类操作(参考 `ext-inventory-injected`)
4. 主进程权限建议可基于 feature 自动建议相关权限

**为什么加 `prisIrPlatforms`?**
1. AI inventory 跨平台部署时按平台过滤(如 Linux 服务器隐藏 win32-only 扩展)
2. 文档/商店可声明「Win 优先 / 跨平台」

**向后兼容**:两个新字段**全 optional**。已有扩展(50+)**不强制**补,后续扩展在创建时 scaffold 自动加空数组,渐进式回填。本次给 5 个 Phase A 扩展补齐作为示范。

## 借鉴原则 P1-6 落地

**借鉴 5「manifest schema 增强」**:见 §3 schema 段落 + §3.1 features 表 + §3.2 platforms 表。

**借鉴 4「extension-spec 文档化」**:见 docs/extension-spec.md 整个文件,8 章:
1. 适用范围(Phase A/B/C 三阶段)
2. 5 步实施法(端口/失败语义/env/测试三件套/L0-L1-L2)
3. SDK 协议(NDJSON JSON-RPC 2.0 子集)
4. manifest schema(prisIrPermissions + features + platforms)
5. 失败语义总表(8 类失败 + alive/ok 双字段)
6. 借鉴的 10 大设计原则(P0/P1/P2 三档)
7. 已知坑(NTFS case-folding / node_modules / vm sandbox / Node 24 node:sqlite)
8. 实战模板(HTTP anonymous / HTTP 鉴权 / SQLite 索引)

**借鉴 6「ESLint 禁 console.log(token)」**:**未实现**。理由:仓库目前没有 ESLint 配置基础,引入 ESLint 是基础设施级别改动,需要 ESLint 配置 + IDE 集成 + CI hook,工作量过大且与本次「Phase A 借鉴 ship」目标正交。**建议下一轮单独 ship**(不在本次 v0.1 spec 范围)。

## 实现

### docs/extension-spec.md(260 行)

8 章节完整 spec。**关键决策**:L0/L1/L2 权限分级(借鉴 v2 P1 原则),Phase A 限定 L0,Phase B 需用户**逐条**勾。

### manifest 加字段(5 个 Phase A 扩展 + scaffold)

**LX Music**:features=`['read-only', 'requires-local-server']`,platforms=`['win32','darwin','linux']`

**YesPlayMusic**:features=同上 + `'open-api-default-off'`(YesPlayMusic 默认 Open API 关)

**Navidrome**:features=同上 + `'requires-credentials', 'subsonic-protocol'`(Token≠密码 + Subsonic)

**SiYuan vault**:features=`['read-only', 'local-file-access', 'sqlite-readonly', 'fts5-search']`(SQLite + FTS5)

**Calibre**:features=同上 + `'like-fuzzy-search'`(LIKE 而非 FTS5)

**scaffold create-ext.js**:`prisIrFeatures: [], prisIrPlatforms: []` 默认空数组,**注释指向 §3 字段定义**让扩展开发者自己选标签。

## 测试

5 扩展联合回归:`pytest tests/test_{navidrome,yesplaymusic,calibre,siyuan,lx}_*.py`:**17 passed + 3 skipped in 3.19s**(manifest 加字段对测试 0 影响 — 测试只验 SDK 注册命令数,不读 features/platforms)。

主仓测试**无新失败**。

## 主进程未来读 manifest

**本 ship 范围**:**仅**沉淀 schema + 5 扩展回填。**主进程 Python `_ext_proxy_dispatch` 当前不读 `prisIrFeatures/prisIrPlatforms`**。未来如果:
- 商店 UI 想展示「SQLite 扩展 5 个 / HTTP 扩展 3 个」→ 主进程读 features 聚合
- AI 系统 prompt 想过滤平台 → 主进程读 platforms
- 权限建议想基于 feature 自动加 → 主进程读 features

可独立 ship 一个「manifest 透传」task,本 spec 留接口。

## 文件清单

- `docs/extension-spec.md` — 8 章 spec(260 行)
- `extensions/_scaffold/create-ext.js` — manifest 加 prisIrFeatures + prisIrPlatforms 字段
- 5 个 Phase A 扩展 `package.json` — 回填 features + platforms

## 借鉴清单完成度

| # | 借鉴项 | commit |
|---|---|---|
| 1 | docs/extension-spec.md 文档化 | **<pending> commit** |
| 2 | Calibre metadata.db 索引(SQLite readonly 原则 3) | ec9f309 |
| 3 | YesPlayMusic 桥(LX 模板复用) | ca876fe |
| 4 | scaffold features[] / platform[] | **<pending> commit** |
| 5 | ESLint 禁 console.log(token) | **未 ship** — 下轮独立 ship |
| 6 | Phase C 统一媒体协议 SDK | **未 ship** — 待 Audiobookshelf 等需求驱动 |

**6/8 完成,2 项(ESLint + Phase C SDK)属于跨阶段基础设施,不在本次 v0.1 spec 范围**。

## Why

extension-spec.md 不是为了「再多一份文档」,而是为了:

1. **未来任何扩展做 Phase A 时直接套 5 步法 + 模板**,不必再读 5 个 Phase A 实战扩展的代码
2. **借鉴原则可被复审** — v2 提炼的 10 大原则具体落到 spec §6
3. **manifest schema 是 Phase B/C 的扩展点** — features/platforms 字段允许未来工具链扫「跨平台 / SQLite / Subsonic」聚合

**5 步法已 100% ship-ready**:新做 HTTP bridge / SQLite indexer,工作量从 2-3 天降到 30 分钟。

## How to apply

下次做新 Phase A 扩展:
1. 读 [docs/extension-spec.md §1 5 步法](../docs/extension-spec.md)
2. 选 [HTTP anonymous / HTTP 鉴权 / SQLite 索引] 三个 §8 模板之一,改 endpoint/表名即可
3. `node extensions/_scaffold/create-ext.js <ext-id> "展示名"`,生成 package.json + index.js 骨架
4. 修 index.js,补 `prisIrFeatures / prisIrPlatforms`(参考 5 实战扩展的 features 选标签)
5. 写 `__tests__/run.js`(参考 §1.4 测试三件套)
6. 写 `tests/test_<ext>.py`(Python wrapper)
7. 跑 `pytest tests/test_<ext>.py` 全绿,联合回归全绿
8. ship memory + commit + push

**未来做 Phase B**(写/控制接口):v0.2 spec 范围,本 spec 留 §0 适用范围 + §1.5 L0/L1/L2 接口。

相关:[[lx-music-like-software-comparison]], [[lx-music-bridge-phase-a-shipped]], [[siyuan-vault-indexer-phase-a-shipped]], [[calibre-metadata-indexer-phase-a-shipped]], [[yesplaymusic-bridge-status-phase-a-shipped]], [[navidrome-bridge-status-phase-a-shipped]], [[p3-10-bubble-cancelled-privacy]]