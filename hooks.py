# -*- coding: utf-8 -*-
# hooks.py — 用户自定义钩子注册表(2026-09-05 P3)
#
# 让 Prisir AI 像 Claude Code 一样支持 hooks:用户在 workdir/hooks.json 声明
# 「什么事件发生时跑什么 shell 命令」,Agent 到点自动执行。
#
# 事件:
#   pre_tool   工具执行前(可阻断)  — 子代事件不外发(子代权限已独立管控)
#   post_tool  工具执行后(只通知)
#   on_response AI 答复完成(只通知)
#   on_error   执行出错(只通知)
#
# hooks.json 格式(workdir 根目录):
#   {
#     "pre_tool":  [{"match": "run_.*", "cmd": "python check.py {tool} {args}"}],
#     "post_tool": [{"match": "write_file|edit_file", "cmd": "git add {path}"}],
#     "on_response": [{"cmd": "echo done >> log.txt"}]
#   }
#   - match: 对工具名的正则(re.search);缺省/空=匹配全部
#   - cmd:   要执行的 shell 命令;占位符 {tool} {args} {path} {output} {error} {event}
#
# 红线:
#   - 命令经 run_shell → 权限闸(写/执行类仍需用户确认,与手工跑一致)
#   - pre_tool 返回码非 0 → 阻断该工具(回读 stderr 给模型,对齐 Claude Code exit 2 语义)
#   - 单条超时 20s、输出截断、执行失败静默放行(post/on_*)或阻断(pre_ 显式非零)
#   - 找不到 hooks.json = 无钩子,零成本
from __future__ import annotations

import json
import os
import re
import subprocess
import threading

_TIMEOUT = 20          # 单条 hook 超时(秒)
_OUT_MAX = 2000        # 回传给模型的 hook 输出上限
_block = threading.local()  # 防重入:hook 命令本身若间接触发工具,不再套 hook


def _hooks_path(workdir: str) -> str:
    return os.path.join(workdir, "hooks.json") if workdir else ""


def load_hooks(workdir: str) -> dict:
    """读 workdir/hooks.json。不存在/解析失败返回 {}。每次调用现读(用户改了就生效)。"""
    p = _hooks_path(workdir)
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — 配置坏了就当没有,绝不阻塞对话
        return {}


def _expand(cmd: str, ctx: dict) -> str:
    """占位符展开。值里的 { 已 json 序列化,安全按 str.format_map 展开,缺键留原样。"""
    class _Safe(dict):
        def __missing__(self, k):
            return "{" + k + "}"
    flat = {k: ("" if v is None else str(v))[:500] for k, v in ctx.items()}
    try:
        return cmd.format_map(_Safe(flat))
    except Exception:  # noqa: BLE001
        return cmd


def _match_one(spec: dict, event: str, ctx: dict) -> bool:
    pat = (spec.get("match") or "").strip()
    if not pat:
        return True
    target = str(ctx.get("tool", ""))
    try:
        return bool(re.search(pat, target))
    except re.error:
        return pat in target


def run_hooks(event: str, workdir: str, ctx: dict | None = None) -> str | None:
    """执行某事件的全部钩子。

    返回:
      pre_tool:  None=放行;  str=阻断理由(返回给模型,工具不执行)
      其他事件:  恒 None(只通知,失败静默)
    """
    if getattr(_block, "active", False):
        return None  # 防重入:hook 内部触发的工具不再套 hook
    ctx = dict(ctx or {})
    ctx["event"] = event
    specs = load_hooks(workdir).get(event) or []
    if not isinstance(specs, list):
        return None
    for spec in specs:
        if not isinstance(spec, dict) or not spec.get("cmd"):
            continue
        if not _match_one(spec, event, ctx):
            continue
        cmd = _expand(str(spec["cmd"]), ctx)
        try:
            _block.active = True
            proc = subprocess.run(cmd, shell=True, cwd=workdir or None,
                                  capture_output=True, text=True, timeout=_TIMEOUT,
                                  encoding="utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            if event == "pre_tool":
                return f"[hook pre_tool 超时 { _TIMEOUT }s 阻断] {cmd[:120]}"
            continue
        except Exception:  # noqa: BLE001
            continue
        finally:
            _block.active = False
        out = ((proc.stdout or "") + (proc.stderr or "")).strip()[:_OUT_MAX]
        if event == "pre_tool" and proc.returncode != 0:
            return (f"[hook pre_tool 阻断 rc={proc.returncode}] {cmd[:120]}\n"
                    f"{out or '(无输出)'}")
    return None


def describe(workdir: str) -> str:
    """给人看的当前钩子摘要(前端设置面板/调试用)。"""
    hooks = load_hooks(workdir)
    if not hooks:
        return ""
    lines = []
    for ev, specs in hooks.items():
        n = len(specs) if isinstance(specs, list) else 0
        lines.append(f"{ev}: {n} 条")
    return "hooks.json · " + ", ".join(lines)
