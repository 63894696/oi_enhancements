'use strict';

/**
 * 正则测试器 v0.1.0
 *
 * 命令:
 *   regex.test { pattern, flags?, text }  → { matches, count, error? }
 *   regex.replace { pattern, flags?, text, replacement } → { result, count }
 *   regex.library { name }               → { pattern, flags, desc, example }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'regex-tester', name: '正则测试器', version: '0.1.0' });

const LIB = {
  email: { pattern: '^[\\w.+-]+@[\\w-]+(?:\\.[\\w-]+)+$', flags: '', desc: 'RFC 简化版邮箱',
    example: 'alice@example.com' },
  phone_cn: { pattern: '^1[3-9]\\d{9}$', flags: '', desc: '中国大陆手机号',
    example: '13800138000' },
  url: { pattern: '^https?://[^\\s]+$', flags: '', desc: 'http/https URL',
    example: 'https://example.com/a?b=1' },
  ipv4: { pattern: '^(?:(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)\\.){3}(?:25[0-5]|2[0-4]\\d|[01]?\\d\\d?)$',
    flags: '', desc: 'IPv4 地址', example: '192.168.1.1' },
  iso_date: { pattern: '^\\d{4}-\\d{2}-\\d{2}$', flags: '', desc: 'ISO 8601 日期',
    example: '2026-09-20' },
  hex_color: { pattern: '^#(?:[0-9a-fA-F]{3}){1,2}$', flags: '', desc: 'HEX 颜色',
    example: '#fdfcf8' },
  semver: { pattern: '^\\d+\\.\\d+\\.\\d+(?:-[\\w.]+)?(?:\\+[\\w.]+)?$', flags: '',
    desc: 'semver 版本号', example: '1.2.3-beta.1' },
};

ext.registerCommand('regex.test', async (args) => {
  try {
    const re = new RegExp(String(args.pattern), String(args.flags || ''));
    const text = String(args.text || '');
    const matches = [];
    let m;
    if (re.global) {
      while ((m = re.exec(text)) !== null) {
        matches.push({
          index: m.index,
          match: m[0],
          groups: m.slice(1),
          named: m.groups || {},
        });
        if (matches.length > 1000) break;   // 防爆
      }
    } else {
      m = re.exec(text);
      if (m) matches.push({ index: m.index, match: m[0], groups: m.slice(1), named: m.groups || {} });
    }
    return { matches, count: matches.length };
  } catch (e) {
    return { error: e.message, matches: [], count: 0 };
  }
});

ext.registerCommand('regex.replace', async (args) => {
  try {
    const re = new RegExp(String(args.pattern), String(args.flags || ''));
    const text = String(args.text || '');
    const result = text.replace(re, String(args.replacement || ''));
    return { result, bytes_in: text.length, bytes_out: result.length };
  } catch (e) {
    return { error: e.message };
  }
});

ext.registerCommand('regex.library', async (args) => {
  if (!args.name) return { items: Object.keys(LIB) };
  const item = LIB[args.name];
  if (!item) return { error: `unknown library: ${args.name}`, items: Object.keys(LIB) };
  return { name: args.name, ...item };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
