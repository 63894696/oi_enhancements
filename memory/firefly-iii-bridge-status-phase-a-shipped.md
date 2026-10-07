---
name: firefly-iii-bridge-status-phase-a-shipped
description: Firefly III 自托管理财 Phase A ship — bearer SDK 第 9 用户 + 跨入个人理财领域 + JSON:API envelope 自动扁平化
metadata:
  type: project
---

# Firefly III 自托管理财 Phase A ship

**2026-10-07 ship,bearer SDK 第 9 用户 + 跨入个人理财领域**(前 8 用户跨 7 类)+ **JSON:API envelope 扁平化通用 helper**。

## Phase C SDK 复用
- **bearer SDK 第 9 用户**(累计跨域 8 类:媒体中心/漫画/书/代码托管/CI/CD/数据分析/云盘/知识/wiki/**个人理财**)
- **零 SDK 边界跨越**: 真 Bearer + 真 REST GET + 100% httpGet SDK 复用
- **新模板 helper**: `flattenAttrs(parsed)` 把 JSON:API envelope `{data: {type, id, attributes: {...}}}` 扁平化 → `{...attributes, _id, _type}`
  处理 2 类 envelope: 单条对象 / 数组列表
  后续任何 JSON:API 风格(RiTa/Ember 等)扩展可直接复用此 helper

## Firefly III 扩展核心(extensions/firefly-iii-bridge-status/)
- **鉴权**: `Authorization: Bearer <personal_access_token>` (RFC 6750,Firefly III Profile → OAuth → PAT)
- **协议**: 真 REST GET + JSON:API envelope + `application/vnd.api+json` content type
- **端点**: `GET /api/v1/about`(系统版本)/ `GET /api/v1/about/user`(当前用户)/ `GET /api/v1/accounts`(账户列表)
- **3 L0 命令**: `firefly.health` / `firefly.user` / `firefly.accounts`
- **绝对不**碰: POST/PUT/DELETE /api/v1/transactions, /api/v1/budgets, /api/v1/accounts/{id}
- **P3.10b 红线**: 100% 本地,0 上传/外传,token 借鉴原则 5 中档(6/10)

## 候选淘汰决策
- **不选 Habitica**: `x-api-user` + `x-api-key` 自定义 header,SDK 边界跨越(给共享 Bearer 中间件开特例)
- **不选 Wekan**: Meteor 风格路径嵌套(`/api/boards/:id/lists`),需 path matcher 支持 `:id` 参数化(资源 ID 解析边界)
- **首选 Firefly III**: 真 Bearer + 真 REST + JSON:API envelope 通用 helper + 跨全新理财域

## JSON:API envelope 解析
```js
function flattenAttrs(parsed) {
  if (parsed.data && typeof parsed.data === 'object' && !Array.isArray(parsed.data)
      && parsed.data.attributes && typeof parsed.data.attributes === 'object') {
    return { ...parsed.data.attributes, _id: parsed.data.id, _type: parsed.data.type };
  }
  if (Array.isArray(parsed.data)) {
    return parsed.data.map((it) => {
      const attrs = (it.attributes && typeof it.attributes === 'object') ? it.attributes : {};
      return { ...attrs, _id: it.id, _type: it.type };
    });
  }
  return null;
}
```

## 测试战绩(12/12 全绿)
- **8 单测**(沙箱 + JSON:API 单元): env override / 无 token 早退 / 不可达 ECONNREFUSED / SDK 5 API 在场
  + `flattenAttrs` 单元: 单条 / 列表 / null / 非 JSON:API 容错
- **4 E2E**(mock Firefly III server @ 随机端口):
  - probeHealth → version=6.1.7 + driver=sqlite + php_version=8.3.0
  - fetchUser → email + is_admin=true(role=owner)
  - fetchAccounts → 4 accounts(asset/expense/revenue) + current_balance + currency_code
  - **错 token → 401 → ok=false + http_status=401 + last_error 含 'HTTP 401'**

## 全量 18 扩展回归
**207/207 passed(37.71s)** + 1 skipped(归档音乐模块)
(比 BookStack ship 后的 203 多 4 个 Firefly Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传、纯只读、不调用麦克风、不外传数据