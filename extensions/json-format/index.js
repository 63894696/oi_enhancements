'use strict';

/**
 * JSON 格式化 v0.1.0
 *
 * 命令:
 *   format.json     { json, indent?, sort_keys? } → { formatted, size }
 *   jsonpath        { json, path }               → { matches, count }
 *   json.validate   { json, schema? }            → { valid, errors }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'json-format', name: 'JSON 格式化', version: '0.1.0' });

function safeParse(s) {
  if (typeof s === 'object') return s;
  try { return JSON.parse(String(s || '')); }
  catch (e) { return { __parseError: e.message }; }
}

// 简化版 JSONPath(只支持 $.a.b[0].c)
function jsonpath(obj, path) {
  const tokens = String(path || '').replace(/^\$\.?/, '').split(/\.|\[(\d+)\]/).filter(Boolean);
  let cur = obj;
  for (const t of tokens) {
    if (cur == null) return [];
    const idx = t.match(/^\d+$/) ? Number(t) : t;
    cur = cur[idx];
  }
  return cur === undefined ? [] : [cur];
}

ext.registerCommand('format.json', async (args) => {
  const parsed = safeParse(args.json);
  if (parsed && parsed.__parseError) {
    return { error: parsed.__parseError };
  }
  const indent = Number(args.indent || 2);
  let obj = parsed;
  if (args.sort_keys) {
    // 浅排序(只顶层)— 深排留给后续版本
    obj = Object.keys(parsed).sort().reduce((o, k) => { o[k] = parsed[k]; return o; }, {});
  }
  const formatted = JSON.stringify(obj, null, indent);
  return { formatted, size: formatted.length, bytes_in: String(args.json).length };
});

ext.registerCommand('jsonpath', async (args) => {
  const parsed = safeParse(args.json);
  if (parsed && parsed.__parseError) {
    return { error: parsed.__parseError };
  }
  const matches = jsonpath(parsed, args.path || '$');
  return { matches, count: matches.length };
});

ext.registerCommand('json.validate', async (args) => {
  const parsed = safeParse(args.json);
  if (parsed && parsed.__parseError) {
    return { valid: false, errors: [parsed.__parseError] };
  }
  // 简化 schema:args.schema = { required: ["a","b"], types: {a:"string"} }
  const errors = [];
  const schema = args.schema || {};
  if (schema.required) {
    for (const k of schema.required) {
      if (!(k in parsed)) errors.push(`missing required field: ${k}`);
    }
  }
  if (schema.types) {
    for (const [k, t] of Object.entries(schema.types)) {
      if (k in parsed) {
        const actual = Array.isArray(parsed[k]) ? 'array' : typeof parsed[k];
        if (actual !== t) errors.push(`type mismatch on ${k}: expected ${t}, got ${actual}`);
      }
    }
  }
  return { valid: errors.length === 0, errors };
});

// onSessionMessage:AI 输出 ```json ...``` 块时,自动注入格式化卡片
ext.onSessionMessage(async (msg, ctx) => {
  if (!ctx.sessionId) return;
  const text = String(msg.text || '');
  const m = text.match(/```json\s*\n([\s\S]+?)\n```/);
  if (!m) return;
  const parsed = safeParse(m[1]);
  if (parsed && parsed.__parseError) return;
  const formatted = JSON.stringify(parsed, null, 2);
  ext.injectCard({
    sessionId: ctx.sessionId,
    cardId: `json-${Date.now()}`,
    html: `<div class="ext-card">
  <div class="ext-card-head">
    <span class="ext-badge">🧩 JSON 格式化</span>
    <span class="ext-title">${formatted.length} 字符</span>
  </div>
  <div class="ext-card-body" style="background:#fdfcf8;padding:14px">
    <pre style="margin:0;font-family:monospace;font-size:12px;color:#2f3a34;white-space:pre-wrap;word-break:break-word;max-height:240px;overflow:auto">${formatted.replace(/</g,'<')}</pre>
  </div>
</div>`,
  });
});

ext.start().catch((e) => {
  console.error('start failed:', e.message);
  process.exit(1);
});
