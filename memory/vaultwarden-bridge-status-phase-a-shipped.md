---
name: vaultwarden-bridge-status-phase-a-shipped
description: Vaultwarden 自托管密码管理器 Phase A ship — bearer SDK 第 11 用户 + 跨入凭据/安全域 + OAuth2 client_credentials 工厂
metadata:
  type: project
---

# Vaultwarden 自托管密码管理器 Phase A ship

**2026-10-07 ship,bearer SDK 第 11 用户 + 跨入凭据/安全域 + 首个 OAuth2 client_credentials 颁发工厂**。

## Phase C SDK 复用
- **bearer SDK 第 11 用户**(累计跨域 11 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/**凭据**)
- **不触 SDK 边界**:SDK 抽象不变,只 inline 一个 `fetchAccessToken()` OAuth2 client_credentials 工厂 30 行
- 工厂在 SDK 上方薄薄一层 — token 颁发走 form-urlencoded POST,数据端走标准 Bearer GET
- 后续 Confluence Cloud / GitLab OAuth2 等同类服务可复用此工厂

## Vaultwarden 扩展核心(extensions/vaultwarden-bridge-status/)
- **鉴权**(两步,与已 ship 10 用户不同):
  - Step 1: `POST /identity/connect/token` (form-urlencoded, OAuth2 client_credentials grant)
    → `{ access_token: <JWT>, expires_in: 3600, token_type: 'Bearer', refresh_token, scope: 'api' }`
  - Step 2: `Authorization: Bearer <jwt>` 调 `/api/...` 数据端
  - token 缓存 3600s(与 Vaultwarden expires_in 对齐)
- **协议**: 真 REST GET + Vaultwarden PascalCase 字段 (`Id`, `Name`, `Email`, `Data`, `TwoFactorEnabled`)
- **端点**:
  - `GET /alive` 公开探活(无需 token,RFC3339 UTC,Docker 健康检查脚本同款)
  - `GET /api/accounts/profile` 当前用户(Id + Email + TwoFactorEnabled + Organizations)
  - `GET /api/folders` 列文件夹(Name 是 EncString,我们只返 Id + RevisionDate + name_encrypted bool)
  - `GET /api/ciphers` 列密码项(Type 1=Login/2=SecureNote/3=Card/4=Identity + Favorite + FolderId + login_uri_count)
- **4 L0 命令**: `vault.health` / `vault.profile` / `vault.folders` / `vault.ciphers`
- **绝对不**碰: `POST /api/accounts/rotate-user-account-keys`(一发改账号)、`/api/ciphers/{id}` POST/PUT/DELETE、`/api/attachments/{id}` upload
- **数据零知识**:服务端仅返 EncString(2.<iv>|<ct>|<mac>),Phase A 我们不强制解密,只取 metadata
- **P3.10b 红线**: 100% 本地,0 上传/外传,token 借鉴原则 5 中档(6/10)

## 候选淘汰决策
- **不选 Uptime Kuma** (上轮): socket.io 主通道 + 无 Bearer 鉴权,触发「REST→socket.io 长连接」SDK 边界跨越
- **首选 Vaultwarden**: 真 OAuth2 client_credentials + 真 REST + 跨入凭据/安全全新域 + 工厂可复用

## 真 bug 修复记录(E2E 第一次 ship 即暴露)
1. **`vaultCredentials()` 返回 `base_url` 而非 `baseUrl`**:`fetchAccessToken({...creds})` 传出去后 `baseUrl` undefined
   → `Cannot read properties of undefined (reading 'replace')`
   修复:`getAccessToken()` 显式映射 `baseUrl: creds.base_url`
2. **stale token 测逻辑错误**:cache 命中不应 401,改测为 cache 命中验证(实际命中 → 走 cache 不发新颁发 → ok=true)

## 测试战绩(15/15 全绿)
- **9 单测**(沙箱): env override / fetchAccessToken 无 credentials/不可达 / 无 credentials probe/profile/folders/ciphers 全 false / SDK 复用 / token cache 形状
- **6 E2E**(mock Vaultwarden server @ 随机端口):
  - probeHealth → alive + server_time + auth.has_credentials=true
  - fetchProfile → Id + Email + TwoFactorEnabled + 2 orgs
  - fetchFolders → 2 folders + EncString 检测 (`name_encrypted` bool)
  - fetchCiphers → 4 ciphers + Type=1 login + FolderId 关联 + login_uri_count
  - **错 client_secret → OAuth2 颁发失败 → 400 + error_description**
  - **token cache 命中 → 走 cache 不发新颁发**

## 全量 20 扩展回归
**215/215 passed(33.53s)** + 1 skipped(归档音乐模块)
(比 Mealie ship 后的 211 多 4 个 Vaultwarden Python wrapper 测试)

## 用户隐私红线遵守(P3.10b)
100% 本地、0 上传、纯只读、不调用麦克风、不外传数据。服务端零知识 — 我们只取 metadata,不解 EncString