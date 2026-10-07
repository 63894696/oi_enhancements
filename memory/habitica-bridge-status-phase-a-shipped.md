---
name: habitica-bridge-status-phase-a-shipped
description: Habitica 自托管习惯/任务/打卡 Phase A ship — custom-auth SDK 第 6 用户 + 跨入习惯域 + 双自定义鉴权头 + x-client 常量
metadata:
  type: project
---

# Habitica 自托管习惯/任务/打卡 Phase A ship

**2026-10-08 ship,custom-auth SDK 第 6 用户 + 跨入习惯/任务/打卡域 + 双自定义鉴权头 + x-client 常量**。

## Phase C SDK 复用
- **custom-auth SDK 第 6 个用户**(累计跨域 6 类:漫画/照片/RSS/媒体中心/凭据/**习惯**)
- **不触 SDK 边界**:SDK `mode='custom'` 抽象 1 对 `(header, token)`,Habitica 需要 3 头(`x-api-user` + `x-api-key` + `x-client`)
- **扩展层 inline wrapper**:`habiticaGet()` thin wrapper 在 SDK httpGet 风格上叠加额外 2 头,SDK 抽象保持不变
- **零边界跨越** — SDK 现有 `mode='custom'` 复用,常量 `x-client` 在扩展内硬编码

## Habitica 扩展核心(extensions/habitica-bridge-status/)
- **鉴权(三头,2025-07 强制)**:
    - `x-api-user: <userId>` UUID(Settings → API → User ID)
    - `x-api-key:  <apiToken>` (Settings → API → API Token)
    - `x-client:  prisirai-prisirai` 固定 author+app 名(防冒充第三方)
  SDK 注入 x-api-key(主鉴权)+ 扩展层 inline 注入 x-api-user 和 x-client
- **协议**: 真 REST GET + `{success:bool, data:object|array, notifications:array}` envelope(扁平 data 即可,无需 flatten helper)
- **端点**:
  - `GET /api/v3/status` 探活(status + notifications_count)
  - `GET /api/v3/user` 当前用户(stats.hp/mp/exp/gp + level + class + sleep_preference + notifications)
  - `GET /api/v3/tasks/user?type=habits|dailys|todos|rewards` 4 类任务(可按 type 过滤)
  - `GET /api/v3/tags` 标签列表
- **4 L0 命令**: `habitica.health` / `habitica.user` / `habitica.tasks{type?}` / `habitica.tags`
- **绝对不**碰: `POST /api/v3/tasks/{id}/score`(打卡扣血) / `POST /api/v3/tasks`(建任务) / `PUT / DELETE` / `POST /api/v3/groups/party/chat`(社交)
- **任务文本截断 200 字符**:防敏感/超大文本扩散(戒酒/服药/减重类可能含医疗)
- **P3.10b 红线**: 100% 本地、0 上传/外传、纯只读、不调用麦克风、不外发用户数据

## 真 bug 修复记录(E2E 第一次 ship 即暴露 2 个)
1. **baseUrl 默认值含 `/api/v3`**:扩展 baseUrl 默认 `http://127.0.0.1:3000/api/v3`,但扩展内 path 又拼 `/api/v3/status`,结果发出 `http://host/api/v3/api/v3/status` → 404
   **修复**:baseUrl 改为 `http://127.0.0.1:3000`(纯 origin),path 拼 `/api/v3/status`(baseUrl 是 origin,path 是 endpoint,职责清晰)
2. **mock server URL 路由没改**:修复 #1 后 mock 仍只匹配 `/status`(无前缀),扩展发 `/api/v3/status` 走 404 分支
   **修复**:mock 路由全部加 `/api/v3` 前缀(`/api/v3/status`, `/api/v3/user`, `/api/v3/tasks/user`, `/api/v3/tags`)
   **教训**:baseUrl vs path 边界清晰后,服务端 mock 也得保持一致 — 修复 #1 是设计正确的,只漏改测试

## 测试战绩(15/15 E2E 全绿)
- **8 单测**(沙箱): env override / probeHealth/user/tasks/tags 无 token + 双鉴权头存在性 / envelope 解包 unwrap / 不可达 ECONNREFUSED
- **7 E2E**(mock Habitica server @ 随机端口):
  - probeHealth → alive + x_client 字段 + notifications_count=1
  - fetchUser → stats.hp/mp/exp/gp + class + sleep_preference + notifications
  - fetchTasks(默认) → 4 任务 + by_type `{habit:1,daily:1,todo:1,reward:1}` 计数
  - fetchTasks(type=dailys) → 只 1 daily 任务(过滤工作)
  - fetchTags → 3 标签 + id/name
  - **错 token → 401 → ok=false + http_status=401**
  - **错 user_id → 401 → ok=false + http_status=401**(双头校验工作)

## 全量 23 扩展回归
**221/221 passed(28.58s)** + 1 skipped(归档音乐模块)
(比 Linkwarden ship 后的 217 多 4 个 Habitica Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传/外传、纯只读、不调用麦克风。token 中档(6/10)— UUID token 一旦签发可重放直到撤销,需用户在 Habitica UI 手动管理

## 候选淘汰决策
- **不选 Uptime Kuma**(上轮):socket.io 主通道 + 无 Bearer 鉴权 = 新 SDK = 边界跨越
- **首选 Habitica**(本轮):鉴权 3 头全靠 custom-auth SDK + inline 注入,envelope 扁平无需 flatten,跨入全新域,实施成本低

## SDK 边界观察
- custom-auth SDK 当前只支持 1 对 `(customHeader, customToken)` 头
- 第二个用户需要 2+ 对头时才会考虑抽象成 `customHeaders: [{name, value}, ...]`
- 当前**保持不变**:Habitica 是 inline 第一个多头需求但应 inline,如果后续 Paperless-ngx 也需多头,再考虑 SDK 抽象升级