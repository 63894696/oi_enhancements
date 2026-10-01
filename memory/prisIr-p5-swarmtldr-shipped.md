---
name: prisIr-p5-swarmtldr-shipped
description: P5-SwarmTLDR 派单 tldr 协议 + 完成报告 marker(借鉴 jcode-swarm-core)
metadata:
  type: project
---

P5-SwarmTLDR(2026-10-02 ship):
- `dev_dispatch.py` —— +134 行(4 常量 + 5 函数)
- `prisIragent_dev_consumer.py` —— +16 行(line 329-344,完成报告校验在 mark_done 之前)
- `tests/test_swarm_tldr.py` —— **27/27 PASS**

4 常量沿用 jcode-swarm-core:
- `SWARM_TLDR_REQUIRED_OVER_CHARS = 240`(body 超 240 字必须带 tldr)
- `MAX_SWARM_TLDR_CHARS = 200`(tldr 上限)
- `SWARM_COMPLETION_REPORT_MARKER = "SWARM COMPLETION REPORT REQUIRED"`
- `MAX_SWARM_COMPLETION_REPORT_CHARS = 4000`

5 新函数:
- `parse_swarm_tldr(content)` —— 行首锚定(中英冒号 + case-insensitive)
- `make_swarm_tldr(tldr)` —— normalize(空白归一 + 截 200)
- `validate_swarm_tldr(content, tldr=None)` —— 校验派单合规
- `validate_completion_report(report)` —— 校验完成报告
- `build_completion_skeleton(tldr, summary, files_touched)` —— 报告模板

**关键设计**:`validate_swarm_tldr` 选择「normalize 后不截断判长度」而非「先截后判」—— spec 要求 300 字 tldr 显式 FAIL,避免静默截断骗 PASS。若上游想「宽容截断」,需 caller 端先 `make_swarm_tldr` 再 `validate`。

**Why**: 大段任务塞死 inbox;tldr 强制项让消费者一眼看到任务关键;collaboration 全链路标准化。

**How to apply**:
- 派单端:`make_swarm_tldr(tldr)` 规范化 → 在 content 行首加 `tldr: ...` → 派单前 `validate_swarm_tldr(content)` 校验
- 完成端:用 `build_completion_skeleton(tldr, summary, files)` 构造报告 → `validate_completion_report` 校验
- consumer:已在 `mark_done` 之前加 `validate_completion_report(reply)` 校验,失败只 `log.warning`(向后兼容存量任务)

边界:
- 不照搬 jcode 1000 worker 上限(PrisirAI dev 通常 <5)
- 不照搬 `validate_swarm_tldr` 内部 normalize 复杂度(直接 trim + 长度判断)
- 不引入新依赖

参考 [[prisIr-p3-hookrisk-shipped]] + [[prisIr-p4-compaction-shipped]]