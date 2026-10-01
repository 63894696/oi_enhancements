---
name: prisIr-p3-hookrisk-shipped
description: P3-HookRisk 命令分级(借鉴 jcode-command-risk,4 档 Safe/Low/Confirm/Catastrophic)
metadata:
  type: project
---

P3-HookRisk(2026-10-02 ship):
- `~/.claude/hooks/command_risk.py` —— 新增 PreToolUse Bash hook,4 档分级
- `~/.claude/hooks/tests/test_command_risk.py` —— **13/13 PASS**
- `~/.claude/settings.json` —— PreToolUse Bash 段在 data_egress 前插入 command_risk(Catastrophic deny 优先)

4 档分级:
- **Safe**:无破坏潜力(curl / cat / ls)
- **Low**:破坏但可恢复(在 cwd / /tmp / git 可回退)
- **Confirm**:静态解析不出(动态变量如 `$HOME` 等)→ allow + reason 警告
- **Catastrophic**:绝对 deny(任何 confirmation 都没撑开)

**关键模式**:
- 8 条 Catastrophic 检测:rm -rf / find -delete / shred / truncate / dd of= / `>file` / mkfs / chmod -R 000
- 11 个保护区:`~` `/etc` `/dev` `/usr` `/var` `/lib` `/bin` `/sbin` `/boot` `/proc` `/sys` `/root` `/`
- **不依赖 shell 解析正确性**(jcode lib.rs:64 原则):就算解析错了 grep 到关键词就 deny

profile 三档沿用 Step 2:
- off(默认):静默
- standard:全部激活
- strict:同 standard(预留 Stop hook)

**Why**: Step 2 P2-Hooks 的 data_egress 只盯 curl/wget/nc 的「网络外发」,漏了 `find -delete` / `shred` / `truncate` / `dd of=/home` / `>file` 重定向等「本地破坏」。Catastrophic 绝对 deny 模型比「警告但不阻断」更安全。

**How to apply**: hook 在 PreToolUse Bash 注册(在 data_egress 之前),Catastrophic deny 在 data_egress 之前生效。

边界:
- 不照搬 jcode 的 shell tokenizer(正则 + path 检查替代,避免复杂度爆炸)
- 不照搬 jcode 的 Confirm + Reflect gate(LLM 二次调用)— PrisirAI 主对话 latency 敏感 + 已有 loop_guard 兜底
- 不照搬 jcode 完整的 protected paths list(只取 11 个核心 + HOME)

参考 [[prisIr-p2-hooks-shipped]] + [[prisIr-p2-rules-shipped]] + [[prisIr-p1-instincts-shipped]]