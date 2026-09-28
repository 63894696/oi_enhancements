---
name: prisIr-skills-workbench-phase-5
description: Skills 工作台 Phase 5 — 前端 skill_plan_request 渲染 + 后端 confirm 分支 ship(2026-09-28)
metadata:
  type: project
---

# Skills 工作台 Phase 5 ship(2026-09-28,commit `9f87c0f`)

承接 [[prisIr-skills-workbench-phase-3-5]] 后端 emit `skill_plan_request` ws 事件;Phase 5 解**前端真正渲染 + 用户确认 + 后端执行闭环**。

## 关键设计:**复用 .cap-confirm 视觉 + 独立多 skill 列表**

不直接复用 `capConfirm` 单 skill 卡片是因为:
- skill_plan_request 含**多个 skill** + risk 列表 + 每个的 args
- 风险徽章取**最大等级**(L3 > L2 > L1 > L0)
- 按钮文案不同(「顺序执行」vs「执行」)

复用策略:**视觉(mask + box + meta 槽)继承 `.cap-confirm`,内容(标题/列表/按钮)独立**。前端 0 重复 CSS 模板,只新加 5 个 skill-plan-* 类。

## 三处改造

### 1. `index.html` 新增 skillPlanConfirm 卡片
```html
<div id="skillPlanConfirm" class="cap-confirm" style="display:none">
  <div class="cap-confirm-mask"></div>
  <div class="cap-confirm-box">
    <div class="cap-confirm-title">🧩 Skills 工作台规划卡</div>
    <div class="cap-confirm-meta">
      <span class="cap-confirm-cap" id="skillPlanCount">0 项</span>
      <span class="cap-confirm-risk" id="skillPlanRisk">L1+</span>
    </div>
    <div class="cap-confirm-body" id="skillPlanBody">...</div>
    <div class="cap-confirm-args" id="skillPlanCalls" style="max-height:240px;overflow:auto"></div>
    <div class="row">
      <button id="skillPlanCancel">取消</button>
      <button class="primary" id="skillPlanOk">我确认,顺序执行</button>
    </div>
  </div>
</div>
```

### 2. `app.js` ws 事件 handler + 函数
```js
} else if (t === "skill_plan_request") {
    showSkillPlanConfirm(m);  // 弹规划卡
} else if (t === "skill_plan_auto_executed") {
    renderSkillPlanAutoExec(m);  // 嵌对话流卡片
} else if (t === "skill_plan_confirm_ack") {
    renderSys("🧩 Skills 规划:" + ...);  // 系统提示
}

function showSkillPlanConfirm(m) {
    // 取最大风险:L0→L1→L2→L3 排序
    // 每行:1. skill_id L2\n   path=x.mp4\n   ...
}

skillPlanOk.click → ws.send({type:"skill_plan_confirm", calls:[...], approved:true})
skillPlanCancel.click → ws.send({type:"skill_plan_confirm", calls:[...], approved:false})
```

### 3. `companion/prisIragent-companion-web.py` skill_plan_confirm 分支
```python
elif t == "skill_plan_confirm":
    approved = bool(data.get("approved", False))
    calls = data.get("calls") or []
    if not approved or not calls:
        emit skill_plan_confirm_ack{reason:"user_cancelled"/"no_calls"}
    else:
        for c in calls:
            r = execute_skill(c.skill_id, c.args, force=True)
            emit capability_exec_result{capability, ok, error, result}  # 复用前端
        emit skill_plan_confirm_ack{approved, executed, total}
```

## CSS 设计要点

`.skill-plan-row` 用 `display:flex + flex-wrap:wrap`,让 idx/sid/risk 横向排列,args `flex-basis:100%` 单独占一行(避免窄屏挤压)。

```css
.skill-plan-row{
  display:flex;flex-wrap:wrap;align-items:center;gap:6px 10px;
  padding:6px 4px;border-bottom:1px dashed var(--line);font-size:13px;
}
.skill-plan-sid{font-weight:600;color:var(--acc);flex:1 1 auto;min-width:120px;word-break:break-all}
.skill-plan-args{flex-basis:100%;padding:2px 0 4px 32px;font-size:11px;color:var(--dim)}
```

风险配色复用 .cap-confirm-* 的 L1/L2/L3 三档:`#6b8e7f` 绿 / `#c79a3a` 金 / `#b65c5c` 红。

## 验证实测

用 puppeteer 模拟 ws 事件触发 `skill_plan_request`:
- 卡片正确弹窗 + 3 个 skill 列表(1.video.cut L1 + 2.publish.html L2 + 3.youtube.upload L2)
- 风险徽章取最大值 L2(金黄色)
- 取消 + 「我确认,顺序执行」按钮在位
- args 缩进显示在 skill_id 下方

## 测试(8/8 绿)
- **#1** index.html 7 DOM id + .cap-confirm 视觉
- **#2** CSS 6 skill-plan-* 类 + L1/L2/L3 3 档配色
- **#3** app.js 7 DOM ref + pendingSkillPlan state
- **#4** app.js 三个 ws 事件 handler 到位
- **#5** app.js 两个新函数 + 风险等级映射(L0/L1/L2/L3)
- **#6** app.js 按钮事件 emit approved:true/false
- **#7** 后端 skill_plan_confirm 分支:execute_skill + capability_exec_result + ack
- **#8** 后端 ack 字段语义:approved/executed/total/reason

## 关键文件
| 文件 | 改动 | 用途 |
|------|------|------|
| `companion/static/index.html` | +60 行 | 卡片 DOM + CSS |
| `companion/static/app.js` | +95 行 | ws 事件 + 函数 + 按钮 |
| `companion/prisIragent-companion-web.py` | +50 行 | skill_plan_confirm 分支 |
| `tests/test_phase_5_skill_plan_ui.py` | +145 行 | 8 测试 |

## 累计测试 **125**
handraw29 + free26 + agencyA5 + SkillsP1 9 + P1.5 6 + P1.6 6 + P1.7 8LLM + P2 9 + P3 8 + P3.5 6 + P4 5 + **P5 8**

## 6 阶段 ship 路径总览
```
Phase 0 — 架构设计 ✓
Phase 1 — skill registry + lazy loader ✓(2026-09-26)
Phase 1.5 — build_messages 接入 ✓(2026-09-27)
Phase 1.6 — 4 类 capability 补齐 ✓(2026-09-28)
Phase 1.7 — LLM 准确率实测 ✓(2026-09-27)
Phase 2 — tool_use 协议适配 ✓(commit f78a64d)
Phase 3 — 两阶段 replan 闸门 ✓(commit 58e8902)
Phase 3.5 — 接入 build_messages ✓(commit 01e9cce)
Phase 4 — EXEC ↔ tool_use 兼容 ✓(commit 639585e)
Phase 5 — 前端渲染 + 后端 confirm ✓(commit 9f87c0f)
```

## 风险登记
1. **WS 未连接时 click 无效** — pendingSkillPlan 是 null,按钮 click 实际啥也不做。改进:让前端对每条 ws 事件都打 log,便于调试
2. **CSS 跨浏览器** — `flex-basis:100%` + `flex-wrap:wrap` 在 Chromium 全 OK,Edge/Firefox 也 OK;老 IE11 不支持(本系统不需要)
3. **执行中断** — `execute_skill` 顺序执行时,**任何一个 fail 会让后续不执行**。要全跑完可加 `continue_on_error=True` 选项

**Why:** Phase 3.5 后端 emit 事件但前端无任何处理,等于不可见;Phase 5 解闭环:用户看到卡 → 点确认 → 后端真发 → 前端看结果。
**How to apply:** 默认配置 `skills_replan_enabled=true` 走完整 replan 链路;否则 Phase 1 直发老路径。下一步可考虑 Phase 6:加 `continue_on_error` 选项 + 全部 80 skill 走 Phase 1.7 那 8 个 case 回归。