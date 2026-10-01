---
name: prisIr-p2-hooks-shipped
description: P2-Hooks 4 个确定性 hook(secrets_check/data_egress/mtime_check/noop_user_prompt) + 三档 profile
metadata:
  type: project
---

P2-Hooks(2026-10-01 ship):
- `.claude/hooks/secrets_check.py` —— PostToolUse Write|Edit|MultiEdit,扫 8 条 secret 模式(AWS AKIA/OpenAI sk-/Anthropic sk-ant-/GitHub ghp_/Slack xox*/PEM private key/Google AIza/generic api_key)
- `.claude/hooks/data_egress.py` —— PreToolUse Bash,扫 4 条数据外发模式(> /tmp/|pipe nc|--upload-file|hex @ + python -c requests.post);loopback 排除
- `.claude/hooks/mtime_check.py` —— PostToolUse 观测,写 `%TEMP%/hook_mtime_<session>.jsonl`(防 dev_consumer 幻影交付)
- `.claude/hooks/noop_user_prompt.py` —— UserPromptSubmit 占位,4 行代码,exit 0
- `.claude/hooks/tests/` —— 22/22 PASS
- `.claude/settings.json` 注册 3 段:PreToolUse Bash→data_egress、PostToolUse Write|Edit|MultiEdit→secrets_check + mtime_check、UserPromptSubmit →noop_user_prompt

profile 三档:
- off(默认):所有 hook 静默 exit 0
- standard:全部激活
- strict:同 standard + Stop hook(stop_test 待 ship,留待 standard 验证)

**Why:** 硬伤检测(明文 key / 数据外发 / mtime)走确定性 hook 不占 token + 防 prompt injection;profile 三档让用户可控强度。
**How to apply:** 用户首次配置向导问 1 次选 profile;新装用户默认 standard,存量用户不动手。

边界:
- 不照搬 ECC strict 默认:PrisirAI 主对话轻量交互多,默认 off
- 不照搬 102 条 AgentShield 规则:只取 8 条 secret + 4 条 egress + mtime,够用即可
- 不照搬 Stop hook:留待 standard 验证后再 ship stop_test.py

参考 [[prisIr-p2-rules-shipped]] + [[prisIr-p1-instincts-shipped]]