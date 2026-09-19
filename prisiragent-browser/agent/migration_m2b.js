// Prisir 智能体 M2b — 一次性迁移器核心(可在 Node 跑通验证)。
//
// 上游:custom-hover-translate/docs/m2-storage-migration-plan-2026-08-14.md §3 迁移流程(已拍板)。
// 定位:把「模型配置 + apiKey + 会话存档」从插件 chrome.storage.local 迁到浏览器 profile
//       级存储(AgentStore,M2a)。本文件是**存储后端无关的迁移逻辑**,真实运行时:
//         - 源:插件侧导出快照(经 extension messaging 或导出文件)
//         - 目标:C++ AgentStore(M2a,经 Mojo)
//       Node 测试时用内存后端替换,逻辑(校验/回滚/幂等/审计脱敏)100% 可测。
//
// 红线(见 M2 §4,已拍板,代码内强制):
//   - apiKey 全程不落明文:迁移读出→(目标侧加密)→落盘,中间态不写明文日志/审计。
//   - apiKey 永不进审计明文:审计里 key 只记 `***(N chars)`。
//   - 迁移不删插件侧数据:只标「本体已接管」,插件副本保留回滚窗(14 天)。
//   - 任一键校验不一致 → 不置 flag、保留插件为权威源、可重试。
'use strict';

// ── 审计脱敏(M2 §4 红线:key 值打码) ────────────────────────────────────
function redact(value) {
  if (value == null) return '(null)';
  const s = String(value);
  return '***(' + s.length + ' chars)';
}

// ── 迁移器 ──────────────────────────────────────────────────────────────
// source: { read(keys[]) → Promise<{key:value}> }       读插件快照
// target: { setConfig({baseURL,model,visionModel,persistHistory}),
//           setApiKey(key), writeThreads(storeObj),
//           getConfig(), getApiKey(), readThreads() }     写/读回 AgentStore(校验用)
// audit:  (event) => void                                审计回调(脱敏)
// opts:   { rollbackWindowDays } 默认 14
async function runMigration(source, target, audit, opts) {
  opts = opts || {};
  const rollbackWindowDays = opts.rollbackWindowDays || 14;
  const log = (ev) => { if (audit) audit(ev); };

  // 1. 读插件侧快照(全部键)。
  const KEYS = ['baseURL', 'apiKey', 'model', 'visionModel', 'persistHistory', 'oiThreads'];
  let snap;
  try {
    snap = await source.read(KEYS);
  } catch (e) {
    log({ stage: 'read_source', ok: false, error: String(e && e.message || e) });
    return { migrated: false, error: 'read_source_failed' };
  }
  snap = snap || {};
  log({
    stage: 'read_source', ok: true,
    baseURL: snap.baseURL ? '(set)' : '(empty)',
    apiKey: redact(snap.apiKey),           // 脱敏,不落明文
    model: snap.model || '(empty)',
    conversations: (snap.oiThreads && snap.oiThreads.order ? snap.oiThreads.order.length : 0),
  });

  // 2. 写入目标(AgentStore)。
  //    非 key 配置 → prefs;apiKey → 加密;oiThreads → threads.json。
  const config = {
    baseURL: snap.baseURL || '',
    model: snap.model || '',
    visionModel: snap.visionModel || '',
    persistHistory: snap.persistHistory !== false,  // 默认开
  };
  try {
    await target.setConfig(config);
    if (snap.apiKey) await target.setApiKey(String(snap.apiKey));  // 目标侧负责加密
    const threads = normalizeThreads(snap.oiThreads);
    await target.writeThreads(threads);
  } catch (e) {
    log({ stage: 'write_target', ok: false, error: String(e && e.message || e) });
    return { migrated: false, error: 'write_target_failed' };
  }
  log({ stage: 'write_target', ok: true });

  // 3. 逐键校验:读回目标 == 插件侧原值。
  const mismatches = [];
  try {
    const rc = await target.getConfig();
    if (rc.baseURL !== config.baseURL) mismatches.push('baseURL');
    if (rc.model !== config.model) mismatches.push('model');
    if (rc.visionModel !== config.visionModel) mismatches.push('visionModel');
    if (rc.persistHistory !== config.persistHistory) mismatches.push('persistHistory');
    if (snap.apiKey) {
      const rk = await target.getApiKey();
      if (rk !== String(snap.apiKey)) mismatches.push('apiKey');  // 只记键名,不记值
    }
    const rt = await target.readThreads();
    if (!threadsEqual(rt, normalizeThreads(snap.oiThreads))) mismatches.push('oiThreads');
  } catch (e) {
    log({ stage: 'verify', ok: false, error: String(e && e.message || e) });
    return { migrated: false, error: 'verify_readback_failed' };
  }

  if (mismatches.length) {
    // 4b. 任一不一致 → 不置 flag、保留插件为权威源、可重试。
    log({ stage: 'verify', ok: false, mismatches });
    return { migrated: false, error: 'verify_mismatch', mismatches };
  }
  log({ stage: 'verify', ok: true });

  // 4a. 全部一致 → 迁移完成。插件侧只读副本保留回滚窗(本函数不删插件数据)。
  const convCount = (snap.oiThreads && snap.oiThreads.order ? snap.oiThreads.order.length : 0);
  log({
    stage: 'done', ok: true, migratedConversations: convCount,
    rollbackWindowDays,
    note: 'plugin copy retained for rollback window; not deleted',
  });
  return {
    migrated: true,
    migratedConversations: convCount,
    rollbackWindowDays,
  };
}

// ── 结构规整:把插件 oiThreads 规整为 {order:[], conversations:{}} ──────────
function normalizeThreads(oiThreads) {
  if (oiThreads && Array.isArray(oiThreads.order) && oiThreads.conversations) {
    return oiThreads;
  }
  return { order: [], conversations: {} };
}

// ── 会话存档等价比较(校验用):order 顺序 + 每个会话的字段与消息逐条比 ────────
function threadsEqual(a, b) {
  a = a || { order: [], conversations: {} };
  b = b || { order: [], conversations: {} };
  const ao = a.order || [], bo = b.order || [];
  if (ao.length !== bo.length) return false;
  for (let i = 0; i < ao.length; i++) if (ao[i] !== bo[i]) return false;
  const ac = a.conversations || {}, bc = b.conversations || {};
  const ak = Object.keys(ac), bk = Object.keys(bc);
  if (ak.length !== bk.length) return false;
  for (const id of ak) {
    if (!bc[id]) return false;
    if (!convEqual(ac[id], bc[id])) return false;
  }
  return true;
}

function convEqual(a, b) {
  if (!a || !b) return false;
  if ((a.title || '') !== (b.title || '')) return false;
  if ((a.createdAt || '') !== (b.createdAt || '')) return false;
  if ((a.updatedAt || '') !== (b.updatedAt || '')) return false;
  const am = a.messages || [], bm = b.messages || [];
  if (am.length !== bm.length) return false;
  for (let i = 0; i < am.length; i++) {
    const x = am[i], y = bm[i];
    if ((x.role || '') !== (y.role || '')) return false;
    if ((x.text || '') !== (y.text || '')) return false;
    const xc = x.citations || [], yc = y.citations || [];
    if (xc.length !== yc.length) return false;
    for (let j = 0; j < xc.length; j++) if (xc[j] !== yc[j]) return false;
  }
  return true;
}

// ── CommonJS + 浏览器双导出 ─────────────────────────────────────────────
const M2B = { runMigration, redact, normalizeThreads, threadsEqual };
if (typeof module !== 'undefined' && module.exports) module.exports = M2B;
if (typeof self !== 'undefined') self.CT_M2B_MIGRATOR = M2B;
