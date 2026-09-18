// Prisir 智能体 — 连续代行链编排器(M3 增强,本体化红利 P0)。
//
// 上游:agent_runtime.mojom(RunActionSequence);registry.js 白名单红线;
//       prisir-native-dividend-automation-2026-08-14.md §4 P0。
// 定位:一条指令编排多个白名单动作顺序执行。宿主无关——执行器(executor)由外部注入:
//       本体态 = AgentRuntime Mojo handler;测试 = 内存 executor。编排逻辑(逐步执行 /
//       L1+ 确认卡拦截 / 暂停续跑 / 审计)100% 可测。
//
// 红线(贯穿,代码内强制):
//   - 只接受 registry 白名单 id;模型不能发明动作(每步先查 REGISTRY)。
//   - L1+ 写操作必须 confirmed=true 才执行;在链里也不跳确认(提示注入防线)。
//   - 每步过 validateParams;参数不合法 → 该步 error,不猜不补。
//   - 全程审计:每步结果 + 链状态都进审计回调(脱敏由调用层 _maskParams 处理)。
//   - 不做无人值守定时链(红利备忘 §3);本编排器只跑「用户触发的一条指令多步」。
'use strict';

// risk 等级排序(对齐 skill_primitives.flowRisk 与 registry risk)。
const RISK_ORDER = { L0: 0, L1: 1, L2: 2, L3: 3 };

// ── 执行一条链 ──────────────────────────────────────────────────────────
// steps: [{id, params, confirmed}]         对齐 mojom SequenceStep
// ctx: {
//   registry:  CT_AGENT_REGISTRY.REGISTRY   白名单表
//   validate:  CT_AGENT_REGISTRY.validateParams
//   executor:  async (id, params) => resultObj   实际执行(Mojo / 内存)
//   confirmPreview: async (id, params) => {valid, summary}   确认卡数据(可选)
//   audit:     (event) => void               审计回调(可选,脱敏由调用层)
// }
// 返回对齐 mojom SequenceResult:
//   { steps:[results], stoppedAt, status, needsConfirmId, confirmPreview }
async function runSequence(steps, ctx) {
  const registry = (ctx && ctx.registry) || {};
  const validate = (ctx && ctx.validate) || ((s, p) => ({ ok: true, params: p }));
  const executor = (ctx && ctx.executor) || (async () => { throw new Error('no_executor'); });
  const audit = (ctx && ctx.audit) || (() => {});
  const results = [];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i] || {};
    const id = step.id;
    const def = registry[id];

    // 红线 1:白名单校验。模型编了 id → 该步 error,链停。
    if (!def) {
      results.push({ ok: false, error: 'not_in_whitelist:' + id });
      audit({ step: i, action: id, result: 'error', reason: 'not_in_whitelist' });
      return { steps: results, stoppedAt: i, status: 'error', needsConfirmId: null, confirmPreview: null };
    }

    // 红线:每步过 schema 校验。
    const v = validate(def.params, step.params);
    if (!v.ok) {
      results.push({ ok: false, error: v.error });
      audit({ step: i, action: id, result: 'error', reason: v.error });
      return { steps: results, stoppedAt: i, status: 'error', needsConfirmId: null, confirmPreview: null };
    }

    // 红线 2:L1+ 必须 confirmed。未确认 → 暂停,回确认卡数据,等用户确认后重放该步。
    const needsConfirm = RISK_ORDER[def.risk] >= RISK_ORDER.L1;
    if (needsConfirm && !step.confirmed) {
      let preview = { valid: true, summary: '确认执行 ' + id + '?' };
      if (ctx.confirmPreview) {
        try { preview = await ctx.confirmPreview(id, v.params); } catch (e) {}
      }
      audit({ step: i, action: id, result: 'needs_confirm' });
      return {
        steps: results, stoppedAt: i, status: 'needs_confirm',
        needsConfirmId: id, confirmPreview: preview,
      };
    }

    // 执行该步。
    try {
      const res = await executor(id, v.params);
      results.push(Object.assign({ ok: true }, res));
      audit({ step: i, action: id, result: 'ok', confirmedBy: needsConfirm ? 'user' : 'auto' });
    } catch (e) {
      results.push({ ok: false, error: String(e && e.message || e) });
      audit({ step: i, action: id, result: 'error', error: String(e && e.message || e) });
      return { steps: results, stoppedAt: i, status: 'error', needsConfirmId: null, confirmPreview: null };
    }
  }

  audit({ result: 'sequence_done', count: results.length });
  return { steps: results, stoppedAt: -1, status: 'done', needsConfirmId: null, confirmPreview: null };
}

// ── 链风险(对齐 skill_primitives.flowRisk):取全链最高 risk,供 UI 决定提示强度 ──
function sequenceRisk(steps, registry) {
  let max = 0;
  for (const s of (steps || [])) {
    const def = registry[s && s.id];
    if (def && RISK_ORDER[def.risk] !== undefined) max = Math.max(max, RISK_ORDER[def.risk]);
  }
  return 'L' + max;
}

// ── 续跑辅助:用户确认某步后,把该步 confirmed 置 true 重放 ────────────────
// 用法:res.status==='needs_confirm' → 用户确认 → resumeSequence(steps, res.stoppedAt, ctx)
function resumeSequence(steps, confirmedIndex, ctx) {
  const next = steps.map((s, i) => i === confirmedIndex ? Object.assign({}, s, { confirmed: true }) : s);
  return runSequence(next, ctx);
}

const SEQ = { runSequence, resumeSequence, sequenceRisk, RISK_ORDER };
if (typeof module !== 'undefined' && module.exports) module.exports = SEQ;
if (typeof self !== 'undefined') self.CT_AGENT_SEQUENCE = SEQ;
