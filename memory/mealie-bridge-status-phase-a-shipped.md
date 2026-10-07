---
name: mealie-bridge-status-phase-a-shipped
description: Mealie 自托管食谱 Phase A ship — bearer SDK 第 10 用户 + 跨入料理/家庭厨房域
metadata:
  type: project
---

# Mealie 自托管食谱 Phase A ship

**2026-10-07 ship,bearer SDK 第 10 用户 + 跨入料理/家庭厨房域**。

## Phase C SDK 复用
- **bearer SDK 第 10 用户**(累计跨域 10 类:媒体中心/漫画/书/代码托管/CI/CD/数据分析/云盘/知识/wiki/个人理财/**食谱**)
- **零 SDK 边界跨越**: 真 Bearer + 真 REST GET + 100% httpGet SDK 复用
- 与 Firefly III 同 pattern(公开探活端点 + 鉴权列表/分页)

## Mealie 扩展核心(extensions/mealie-bridge-status/)
- **鉴权**: `Authorization: Bearer <long_lived_api_token | JWT>` (RFC 6750,Mealie User Profile → API Tokens 或 OAuth2PasswordBearer form JWT)
- **协议**: 真 REST GET + 轻量 JSON:API 风格包装 `{page, per_page, total, total_pages, data:[...], next, previous}`
- **端点**: 
  - `GET /api/app/about` 公开探活(Mealie 不检查 token,但 SDK 统一要求非空)
  - `GET /api/users/self` 当前用户(id + email + admin + groupId + householdId)
  - `GET /api/recipes?page=N&perPage=M` 鉴权后分页列食谱
- **3 L0 命令**: `mealie.health` / `mealie.user` / `mealie.recipes{page?, perPage?}`
- **绝对不**碰: POST /api/recipes / PUT /api/recipes/{slug} / DELETE /api/recipes/{slug}
- **P3.10b 红线**: 100% 本地,0 上传/外传,token 借鉴原则 5 中档(6/10)

## 候选淘汰决策
- **不选 Uptime Kuma**: socket.io 主通道 + 无 Bearer 鉴权(除 /metrics),需新建 socket.io-client SDK
  - 工程量触发 SDK 边界跨越(从「REST 协议栈」迈入「socket.io 长连接」栈),违反「避免引入全新 SDK 类型」原则
  - Phase A 决定暂不 ship 任何需要全新协议栈的扩展
- **首选 Mealie**: 真 Bearer + 真 REST + 跨入料理/家庭厨房全新域(累计 10 域)

## 设计决策:SDK 早退语义
- 公开端点 `GET /api/app/about` Mealie 不检查 token,但 SDK 统一要求 token 非空
- → 用户完全没配 token 时,`probeHealth` 也会 SDK 早退 `last_error='no credentials'`
- 这是「统一 SDK 边界行为」选择:任何 probe 都要求 token 配齐,避免 user surprise
- 修复历史:Kavita 模式后第一次失败即在第一次 ship 暴露,验证了 E2E + 401 错 token 测试链路价值

## 测试战绩(12/12 全绿)
- **7 单测**(沙箱): env override / 无 token 早退 / 不可达 ECONNREFUSED / SDK 5 API 在场 / 入参钳制
- **5 E2E**(mock Mealie server @ 随机端口):
  - probeHealth → version=v3.17.0 + demo_status=false + token_time_minutes
  - fetchUser → email + admin=true + household_id
  - fetchRecipes(默认) → 3 recipes + rating + tags + recipe_category + total_pages
  - fetchRecipes(page=1, perPage=2) → 2 recipes + total_pages=2 + next=`?page=2`
  - **错 token → 401 → ok=false + http_status=401 + last_error 含 'HTTP 401'**

## 全量 19 扩展回归
**211/211 passed(36.80s)** + 1 skipped(归档音乐模块)
(比 Firefly III ship 后的 207 多 4 个 Mealie Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传、纯只读、不调用麦克风、不外传数据