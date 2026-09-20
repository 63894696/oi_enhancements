'use strict';

/**
 * 时间戳互转 v0.1.0
 *
 * 命令:
 *   ts.now {}                     → { unix_ms, unix_s, iso, zh, rfc2822, tz }
 *   ts.convert { input }          → { unix_ms, unix_s, iso, zh, rfc2822, tz }
 *   ts.rel    { unix_ms? | iso? } → { seconds_ago, human }
 */

const { PrisIrExt } = require('@prisir/extension-sdk');
const ext = new PrisIrExt({ id: 'timestamp', name: '时间戳互转', version: '0.1.0' });

const ZH_WEEK = ['日','一','二','三','四','五','六'];
function fmtZh(d) {
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())} ` +
         `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())} ` +
         `周${ZH_WEEK[d.getDay()]}`;
}
function fmtRfc(d) { return d.toUTCString(); }
function fmtIso(d) { return d.toISOString(); }

function fromAny(input) {
  if (input == null) return new Date();
  if (input instanceof Date) return input;
  if (typeof input === 'number') return new Date(input < 1e12 ? input * 1000 : input);
  const s = String(input).trim();
  // unix(纯数字)
  if (/^\d{10}$/.test(s)) return new Date(Number(s) * 1000);
  if (/^\d{13}$/.test(s)) return new Date(Number(s));
  return new Date(s);   // ISO / RFC / 中文都让 Date 解析
}

function shape(d) {
  return {
    unix_ms: d.getTime(),
    unix_s: Math.floor(d.getTime() / 1000),
    iso: fmtIso(d),
    zh: fmtZh(d),
    rfc2822: fmtRfc(d),
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone || 'local',
  };
}

ext.registerCommand('ts.now', async () => shape(new Date()));

ext.registerCommand('ts.convert', async (args) => shape(fromAny(args.input)));

ext.registerCommand('ts.rel', async (args) => {
  const d = fromAny(args.input ?? args.unix_ms ?? args.iso);
  const diff = Math.floor((Date.now() - d.getTime()) / 1000);
  let human;
  const abs = Math.abs(diff);
  if (abs < 60) human = `${diff} 秒`;
  else if (abs < 3600) human = `${Math.round(diff / 60)} 分钟`;
  else if (abs < 86400) human = `${Math.round(diff / 3600)} 小时`;
  else if (abs < 86400 * 30) human = `${Math.round(diff / 86400)} 天`;
  else if (abs < 86400 * 365) human = `${Math.round(diff / (86400 * 30))} 个月`;
  else human = `${Math.round(diff / (86400 * 365))} 年`;
  return { seconds_ago: diff, human: (diff >= 0 ? `${human}前` : `${human}后`) };
});

ext.start().catch((e) => { console.error(e.message); process.exit(1); });
