'use strict';

/**
 * @prisir/extension-sdk v0.1.0
 * PrisirAI 扩展开发 SDK — stdio NDJSON JSON-RPC 客户端封装。
 *
 * 用法(index.js 入口):
 *   const { PrisIrExt, command, panel, fileProvider, onSessionMessage } = require('@prisir/extension-sdk');
 *   const ext = new PrisIrExt({ id: 'mermaid', name: '设计稿渲染', version: '0.1.0' });
 *   ext.registerCommand('render.mermaid', async (args, ctx) => ({ html: '...' }));
 *   await ext.start();
 *
 * 协议(跟 docs/prisir-extension-roadmap-2026-09-20.md §4.2 一致):
 *   主 → ext: 每行一个 JSON 对象(stdin NDJSON)
 *     {"jsonrpc":"2.0","id":N,"method":"...","params":{...}}   ← request
 *     {"jsonrpc":"2.0","method":"...","params":{...}}            ← notification
 *   ext → 主: 每行一个 JSON 对象(stdout NDJSON)
 *     同样格式
 *
 * 主进程会主动推:
 *   - initialize 通知(握手)
 *   - config.changed 通知(settings 改了)
 *
 * 扩展可主动调:
 *   - registerCommand 注册的命令(主进程 invoke 时触发)
 *   - ui.inject 通知:把 HTML/SVG 卡片塞进主会话
 *   - log 通知:打日志
 */

const readline = require('readline');

class PrisIrExt {
  constructor(meta) {
    if (!meta || !meta.id) {
      throw new Error('PrisIrExt requires { id }');
    }
    this.meta = {
      id: String(meta.id),
      name: String(meta.name || meta.id),
      version: String(meta.version || '0.0.0'),
    };
    this.commands = new Map();      // method → handler(args, ctx) → result
    this.panels = new Map();        // panel_id → { title, icon, render }
    this.fileProviders = new Map(); // scheme → handler(path) → Buffer
    this.hooks = {
      onSessionMessage: [],         // (msg, ctx) → void
      onSessionStart: [],
      onSessionEnd: [],
    };
    this._msgId = 0;
    this._pending = new Map();      // id → { resolve, reject, timer }
    this._rl = null;
  }

  // ──────────────── 注册 API(链式) ────────────────
  registerCommand(method, handler) {
    if (typeof handler !== 'function') {
      throw new Error(`registerCommand(${method}): handler must be function`);
    }
    this.commands.set(method, handler);
    return this;
  }

  registerPanel(id, def) {
    if (!def || typeof def.render !== 'function') {
      throw new Error(`registerPanel(${id}): def.render must be function`);
    }
    this.panels.set(id, { title: def.title || id, icon: def.icon || '🧩', render: def.render });
    return this;
  }

  registerFileProvider(scheme, handler) {
    if (typeof handler !== 'function') {
      throw new Error(`registerFileProvider(${scheme}): handler must be function`);
    }
    this.fileProviders.set(scheme, handler);
    return this;
  }

  onSessionMessage(fn)  { this.hooks.onSessionMessage.push(fn);  return this; }
  onSessionStart(fn)    { this.hooks.onSessionStart.push(fn);    return this; }
  onSessionEnd(fn)      { this.hooks.onSessionEnd.push(fn);      return this; }

  // ──────────────── 跨扩展调用 (Phase B-1, 2026-09-20) ────────────────
  /**
   * 调另一个扩展的命令。协议:
   *   ext → 主: notification extension.invoke_request {req_id, target_ext_id, method, params}
   *   主 → ext: notification extension.invoke_response {req_id, result | error}
   * 主进程转发层 (Python _ext_proxy_dispatch) 负责实际调用 _ext_rpc_call,
   * 把 result 推回本 ext 的 _pending 通道(req_id 配对)。
   *
   * 失败情况:
   *   - RPC 超时(timeoutMs 到)→ reject 'invokeExt timeout: extId.method'
   *   - 目标扩展未启用 → reject 'extension not enabled'
   *   - 目标方法不存在 → reject 'unknown method: method'(主进程 _ext_rpc_call 返回 None 后转发 error)
   */
  async invokeExt(extId, method, params = {}, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
      const reqId = `ext_${this._nextId()}_${Math.random().toString(36).slice(2, 8)}`;
      const timer = setTimeout(() => {
        if (this._pending.has(reqId)) {
          this._pending.delete(reqId);
          reject(new Error(`invokeExt timeout: ${extId}.${method}`));
        }
      }, timeoutMs);
      // 用 try/catch 包 resolve 防止 timer 已触发后业务又 resolve
      this._pending.set(reqId, {
        resolve: (r) => { clearTimeout(timer); resolve(r); },
        reject:  (e) => { clearTimeout(timer); reject(new Error(String(e))); },
      });
      this._notify('extension.invoke_request', {
        req_id: reqId,
        target_ext_id: String(extId),
        method: String(method),
        params: params || {},
      });
    });
  }

  // ──────────────── 主动通知主进程 ────────────────
  injectCard({ sessionId, cardId, html, type = 'card' }) {
    if (!sessionId) throw new Error('injectCard: sessionId required');
    this._notify('ui.inject', { session_id: sessionId, card_id: cardId || '', html: html || '', type });
  }

  log(level, msg) {
    this._notify('log', { level: String(level || 'info'), msg: String(msg || '') });
  }

  // ──────────────── 启动 ────────────────
  async start() {
    process.stdout.write(JSON.stringify({
      jsonrpc: '2.0', method: 'log',
      params: { level: 'info', msg: `${this.meta.id}@${this.meta.version} starting` },
    }) + '\n');
    this._rl = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
    this._rl.on('line', (line) => this._onLine(line));
    process.on('SIGTERM', () => process.exit(0));
    process.on('SIGINT', () => process.exit(0));
  }

  // ──────────────── 内部 ────────────────
  _nextId() { this._msgId += 1; return this._msgId; }

  _notify(method, params) {
    process.stdout.write(JSON.stringify({ jsonrpc: '2.0', method, params: params || {} }) + '\n');
  }

  _reply(id, resultOrError, isError = false) {
    const payload = { jsonrpc: '2.0', id };
    if (isError) payload.error = { code: -1, message: String(resultOrError) };
    else payload.result = resultOrError;
    process.stdout.write(JSON.stringify(payload) + '\n');
  }

  _onLine(line) {
    const trimmed = String(line || '').trim();
    if (!trimmed) return;
    let msg;
    try { msg = JSON.parse(trimmed); }
    catch (e) {
      this.log('warning', `bad json from main: ${trimmed.slice(0, 120)}`);
      return;
    }
    // notification(无 id)
    if (msg.method && !('id' in msg)) {
      this._handleNotification(msg).catch((e) => this.log('error', `notif err: ${e.message}`));
      return;
    }
    // request(有 id)
    if (msg.method && 'id' in msg) {
      this._handleRequest(msg).catch((e) => this._reply(msg.id, e.message, true));
      return;
    }
  }

  async _handleNotification(msg) {
    const { method, params = {} } = msg;
    if (method === 'initialize') {
      this.log('info', `initialized (main session=${params.session_id || 'n/a'})`);
      return;
    }
    if (method === 'config.changed') {
      this._onConfigChanged(params.settings || {});
      return;
    }
    // Phase B-1: 跨扩展调用的响应(主进程转发 _ext_proxy_dispatch 推回)
    if (method === 'extension.invoke_response') {
      const reqId = params.req_id;
      const p = this._pending.get(reqId);
      if (p) {
        this._pending.delete(reqId);
        if (params.error) p.reject(params.error);
        else p.resolve(params.result === undefined ? null : params.result);
      }
      return;
    }
    if (method === 'event') {
      const evType = params.type;
      const ctx = { sessionId: params.session_id || '', ext: this };
      if (evType === 'session.message' || evType === 'session.message.assistant') {
        for (const fn of this.hooks.onSessionMessage) {
          try { await fn(params, ctx); }
          catch (e) { this.log('error', `onSessionMessage hook err: ${e.message}`); }
        }
      } else if (evType === 'session.start') {
        for (const fn of this.hooks.onSessionStart) await fn(params, ctx);
      } else if (evType === 'session.end') {
        for (const fn of this.hooks.onSessionEnd) await fn(params, ctx);
      }
    }
  }

  _onConfigChanged(settings) {
    // 子类可覆盖。默认 no-op
    this._settings = settings;
    this.log('info', `config updated: ${Object.keys(settings).join(',')}`);
  }

  async _handleRequest(msg) {
    const { id, method, params = {} } = msg;
    const handler = this.commands.get(method);
    if (!handler) {
      this._reply(id, `unknown method: ${method}`, true);
      return;
    }
    const ctx = { sessionId: params.session_id || '', ext: this };
    try {
      const result = await handler(params, ctx);
      this._reply(id, result === undefined ? null : result);
    } catch (e) {
      this._reply(id, e.message || String(e), true);
    }
  }
}

module.exports = { PrisIrExt };
