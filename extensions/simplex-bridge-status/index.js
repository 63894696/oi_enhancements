'use strict';

/**
 * simplex-bridge-status v0.1.0 — SimpleX 跨设备 E2E 通信 metadata(只读, Bearer Token)
 *
 * 数据来源: SimpleX Chat CLI(http://127.0.0.1:5225, simplex-chat v6.x)。
 *            SimpleX 是 simplex-chat/simplex-chat(AGPL-3.0)开发的真正 E2EE 跨设备通信协议,
 *            无用户 ID 概念(每会话独立 1 次性 link),SMP 服务器只中转加密消息看不到内容,
 *            双棘轮 + X3DH 端到端加密。客户端形态:CLI / Desktop / iOS / Android,均可启 5225 端口。
 *            鉴权: SimpleX CLI 6.0+ 支持 `--api-token=<long-random-token>`,默认绑 127.0.0.1。
 *
 * **Phase C SDK 复用(2026-10-10)**:SimpleX 是 bearer-client.js SDK **第十五个用户**
 * (前 14 用户跨 14 类:媒体/漫画/书/代码/CI/分析/云/wiki/知识/理财/食谱/凭据/书签/笔记/协同笔记)。
 * **跨入跨设备/E2E 通信域**作为第 16 类(填补 26 个扩展 0 通信域空白)。
 * **零 SDK 边界跨越**(与 Linkwarden/Mealie/BookStack/Trilium/HedgeDoc 同 pattern:
 * `httpGet + describeAuth + makeConfig`)。
 *
 * **envelope 形状**:SimpleX CLI 6.x 返 `{result: {type: "contactsList", contacts: [...]}}`,
 * 第一层 unwrap 抽 `result`。
 *
 * **License** AGPL-3.0(同 [[agpl-ship-boundary]] 客户端代理 ship 边界)— 纯只读 GET/POST +
 * 进程级隔离,客户端 HTTP 集成不触发传染。
 *
 * **借鉴原则 5「Token≠密码」最低档(3/10)** — 本地 token 只绑 127.0.0.1,无吊销机制,
 * 用户改 `--api-token` 重启 SimpleX CLI 即可。私钥物理隔离在 `~/.simplex/`,扩展不读。
 *
 * 命令(L0 风险, 纯只读 metadata):
 *   simplex.health    {}  → GET /  探活(API 状态)
 *   simplex.contacts  {search?, limit?}  → POST /v3/contacts 列联系人
 *   simplex.chats     {chatType?, limit?}  → POST /v3/chats 列聊天列表
 *   simplex.groups    {limit?}  → POST /v3/groups 列群组
 *
 * **绝不**触碰:
 *   - 任何带 `text` / `formattedText` / `file` 字段的端点(productive 消息)
 *   - 任何 `POST /v3/{send,delete,update,...}` 写端点
 *   - 私钥文件(`~/.simplex/`)直读 — 只能经 SimpleX CLI HTTP API
 *
 * P0 安全约束(产品级):
 *   1. **只白名单 POST**(`/v3/contacts`、`/v3/chats`、`/v3/groups`)+ `GET /` 探活
 *   2. **不抓 content**(`text` / `formattedText` / `file` 字段绝不返回)— 类比 Trilium/HedgeDoc
 *   3. **0 上传/外传**(本地 127.0.0.1,扩展进程级隔离)
 *   4. 用户须自起 SimpleX CLI + 配 `--api-token`,不绕过本地 token
 *
 * env 配置(由主对话 / 用户在主壳 web.py 配置覆盖, 不入 git):
 *   PRISIR_SIMPLEX_URL      默认 http://127.0.0.1:5225
 *   PRISIR_SIMPLEX_API_KEY  SimpleX CLI `--api-token=<token>` 长随机字符串
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const { makeConfig, httpGet, httpPostJson, describeAuth } = require('../_scaffold/bearer-client');

// ── env 字段注入 + Bearer SDK config ──────────────────────
function simplexConfig() {
  return makeConfig({
    baseUrl: process.env.PRISIR_SIMPLEX_URL || 'http://127.0.0.1:5225',
    token: process.env.PRISIR_SIMPLEX_API_KEY || '',
    timeoutMs: 5000,
  });
}

// SimpleX CLI 返 envelope: {resp: {type: "...", ...}, corrId: "..."} 或 {result: {type: "contactsList", contacts: [...]}}
// 第一层 unwrap 抽 resp 或 result
function unwrapSimpleX(parsed) {
  if (parsed && typeof parsed === 'object') {
    if (parsed.result) return parsed.result;
    if (parsed.resp) return parsed.resp;
  }
  return parsed;
}

// ── 探活:GET / ──────────────────────────────────────
async function probeHealth() {
  const cfg = simplexConfig();
  const t0 = Date.now();
  const r = await httpGet({ config: cfg, path: '/' });
  const dt = Date.now() - t0;
  if (!r.ok) {
    return {
      ok: false, alive: false,
      simplex_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
      auth: describeAuth(cfg), last_error: r.error || `HTTP ${r.status}`,
    };
  }
  return {
    ok: true, alive: true,
    simplex_url: cfg.baseUrl_(), latency_ms: dt, http_status: r.status,
    auth: describeAuth(cfg),
    last_error: '',
  };
}

// ── POST /v3/contacts 列联系人(不抓 content)────────────
async function fetchContacts(args = {}) {
  const cfg = simplexConfig();
  // SimpleX CLI 期望: {type: "contactsList"} 或空 body,返回 {resp: {type: "contactsList", contacts: [...]}}
  const body = { type: 'contactsList' };
  const r = await httpPostJson({ config: cfg, path: '/v3/contacts', body });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const data = unwrapSimpleX(r.parsed) || {};
  const list = Array.isArray(data.contacts) ? data.contacts : [];
  return {
    ok: true, alive: true,
    contacts: list.map((c) => ({
      contact_id: Number(c.contactId) || 0,
      local_display_name: String(c.localDisplayName || ''),
      profile_display_name: String((c.profile && c.profile.displayName) || ''),
      profile_full_name: String((c.profile && c.profile.fullName) || '').slice(0, 200),
      is_user: Boolean(c.isUser),
      is_contact: !c.isUser,                   // 联系人 vs 用户自己
      active: Boolean(c.activeConn !== null && c.activeConn !== undefined),
      // **不抓** profile.contactLink / profile.image — 隐私敏感
      has_profile_image: Boolean(c.profile && c.profile.image),
    })),
    total: list.length,
    last_error: '',
  };
}

// ── POST /v3/chats 列聊天列表(不抓 content)───────────
async function fetchChats(args = {}) {
  const cfg = simplexConfig();
  const chatType = String(args.chatType || '').toLowerCase();
  // chatType: 'p2p'(私聊) / 'group'(群组) / '' 全部
  const body = { type: 'chatsList' };
  if (chatType === 'p2p' || chatType === 'group') body.chatType = chatType;
  const r = await httpPostJson({ config: cfg, path: '/v3/chats', body });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const data = unwrapSimpleX(r.parsed) || {};
  const list = Array.isArray(data.chats) ? data.chats : [];
  return {
    ok: true, alive: true,
    chats: list.map((c) => {
      const info = c.chatInfo || {};
      return {
        chat_type: String(info.type || 'unknown'),
        chat_id: Number(c.chatId) || 0,
        local_display_name: String(info.localDisplayName || '').slice(0, 200),
        contact_id: Number(info.contactId) || 0,    // type=p2p 时
        group_id: Number(info.groupId) || 0,        // type=group 时
        // 不抓 unreadCount(可能含消息预览)
        // 不抓 chatItem.content.text / file
        has_metadata_only: true,
      };
    }),
    total: list.length,
    last_error: '',
  };
}

// ── POST /v3/groups 列群组(不抓 content)─────────────
async function fetchGroups(args = {}) {
  const cfg = simplexConfig();
  const body = { type: 'groupsList' };
  const r = await httpPostJson({ config: cfg, path: '/v3/groups', body });
  if (!r.ok) {
    return { ok: false, alive: false, http_status: r.status, last_error: r.error || `HTTP ${r.status}` };
  }
  const data = unwrapSimpleX(r.parsed) || {};
  const list = Array.isArray(data.groups) ? data.groups : [];
  return {
    ok: true, alive: true,
    groups: list.map((g) => ({
      group_id: Number(g.groupId) || 0,
      local_display_name: String(g.localDisplayName || '').slice(0, 200),
      display_name: String(g.displayName || '').slice(0, 200),
      full_name: String(g.fullName || '').slice(0, 200),
      member_count: Number(g.membership) ? 1 : 0,  // 占位
      // **不抓** groupProfile.description / image / memberDetails.contactProfile
      has_description: Boolean(g.groupProfile && g.groupProfile.description),
    })),
    total: list.length,
    last_error: '',
  };
}

// ── 注册 extension ─────────────────────────────────────
const ext = new PrisIrExt({
  id: 'simplex-bridge-status',
  name: 'SimpleX 跨设备 E2E 通信 metadata(只读, Bearer Token)',
  version: '0.1.0',
});

ext.registerCommand('simplex.health', async () => probeHealth());
ext.registerCommand('simplex.contacts', async (args) => fetchContacts(args || {}));
ext.registerCommand('simplex.chats', async (args) => fetchChats(args || {}));
ext.registerCommand('simplex.groups', async (args) => fetchGroups(args || {}));

ext.start().catch((e) => { console.error(e.message); process.exit(1); });

// ── 测试钩子 ──────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    probeHealth, fetchContacts, fetchChats, fetchGroups,
    simplexConfig, unwrapSimpleX,
  };
}