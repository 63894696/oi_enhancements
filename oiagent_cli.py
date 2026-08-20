"""oiagent-cli — minimal conversational agent shell for SPIKE #2 benchmark.

Bridges the gap: oiagent has MCP tools + GUI shell but no headless
"task -> autonomous execute -> deliver" CLI. This is the minimal loop:

  prompt -> LLM (litellm / OpenRouter) -> tool_call -> dispatch -> feed back -> repeat

Tools (code-editing, not the ops MCP set): read_file / write_file / run_shell / list_files.
Self-contained: only needs litellm on the instance. Keys via env (OPENROUTER_API_KEY) only.

CLI mirrors aider so spike2-run-with-keys.py can drive it:
  python oiagent_cli.py --message-file PROMPT --model MODEL [--max-turns N] [--workdir DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

MAX_TURNS_DEFAULT = 30
SHELL_TIMEOUT = 60


# ---------------- tools ----------------
def _t_read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception as e:  # noqa: BLE001
        return f"[read_file error] {e}"


def _t_write_file(path: str, content: str) -> str:
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"[write_file ok] {path} ({len(content)} bytes)"
    except Exception as e:  # noqa: BLE001
        return f"[write_file error] {e}"


def _t_run_shell(command: str, workdir: str) -> str:
    try:
        r = subprocess.run(command, cwd=workdir, capture_output=True,
                           timeout=SHELL_TIMEOUT, shell=True)
        out = (r.stdout or b"").decode("utf-8", "replace")
        err = (r.stderr or b"").decode("utf-8", "replace")
        return f"[rc={r.returncode}]\nstdout:\n{out}\nstderr:\n{err}"
    except subprocess.TimeoutExpired:
        return "[run_shell timeout]"
    except Exception as e:  # noqa: BLE001
        return f"[run_shell error] {e}"


def _t_list_files(path: str, workdir: str) -> str:
    try:
        base = os.path.join(workdir, path) if not os.path.isabs(path) else path
        items = []
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if d not in ("__pycache__", ".pytest_cache", ".git")]
            for fn in files:
                items.append(os.path.relpath(os.path.join(root, fn), workdir))
            if len(items) > 200:
                break
        return "\n".join(sorted(items)) or "[empty]"
    except Exception as e:  # noqa: BLE001
        return f"[list_files error] {e}"


def _completion_with_temperature_fallback(**kwargs):
    """调 litellm.completion,对「temperature 取值受限」的模型自动降级重试。

    部分平台(如 kimi coding 系列)只接受固定 temperature(如仅允许 1),
    对硬编码的 0.7/0 报 "invalid temperature: only 1 is allowed"。
    策略:先用期望温度;若报 invalid temperature,则改为「不带 temperature」让服务端用默认。
    配合 litellm.drop_params=True,不支持的参数会被丢弃而不是报错。
    """
    import litellm
    litellm.drop_params = True
    try:
        return litellm.completion(**kwargs)
    except Exception as e:  # noqa: BLE001
        msg = str(e).lower()
        if "temperature" in msg and ("invalid" in msg or "only" in msg or "not supported" in msg):
            kwargs.pop("temperature", None)
            return litellm.completion(**kwargs)
        raise


TOOLS = [
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a text file and return its contents.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "write_file",
        "description": "Write text content to a file (creates parent dirs).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_shell",
        "description": "Run a shell command in the working directory and return rc/stdout/stderr.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}}},
    {"type": "function", "function": {
        "name": "list_files",
        "description": "List files under a path (relative to workdir).",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
]


def dispatch(name: str, args: dict, workdir: str) -> str:
    if name == "read_file":
        p = args.get("path", "")
        p = p if os.path.isabs(p) else os.path.join(workdir, p)
        return _t_read_file(p)
    if name == "write_file":
        p = args.get("path", "")
        p = p if os.path.isabs(p) else os.path.join(workdir, p)
        return _t_write_file(p, args.get("content", ""))
    if name == "run_shell":
        return _t_run_shell(args.get("command", ""), workdir)
    if name == "list_files":
        return _t_list_files(args.get("path", "."), workdir)
    return f"[unknown tool {name}]"


SYSTEM = (
    "You are oiagent, an autonomous coding agent in a benchmark. "
    "Work fully autonomously: use the provided tools to read files, write files, and run shell commands. "
    "Never ask the user for confirmation or for files — everything you need is in the working directory. "
    "Always create the exact output files the task requires. "
    "When the task is 100% complete and all required files are written, reply with the single word DONE."
)

# 对话模式(Prisir AI):助手问答,不强制 DONE,保留多轮历史,工具可选
SYSTEM_CHAT = (
    "You are oiagent, a helpful AI assistant in Prisir Browser (no-account, local-first). "
    "Answer the user's questions directly and clearly, in the user's language. "
    "You may use the provided tools to read/write files or run shell commands when the task needs it, "
    "but for pure Q&A just answer in prose. Keep answers focused and well-structured."
)


def run_conversation(messages: list, model: str, workdir: str, max_turns: int = 6,
                     use_tools: bool = True) -> dict:
    """对话模式:接受完整多轮 messages([{role,content}]),返回单轮 assistant 回复。

    与 run_agent 的区别:
      - 不注入"自主任务 + DONE"系统提示,改用 SYSTEM_CHAT 助手提示
      - 返回最后一轮 assistant content,不循环到 DONE
      - 仍允许少量工具调用(max_turns 内),但收到无工具调用的文本回复即返回
    """
    import litellm
    litellm.drop_params = True
    msgs = [{"role": "system", "content": SYSTEM_CHAT}] + list(messages)
    t0 = time.time()
    turns = 0
    tools = TOOLS if use_tools else None
    for _ in range(max(1, max_turns)):
        turns += 1
        try:
            kwargs = dict(model=model, messages=msgs, temperature=0.7)
            if tools:
                kwargs.update(tools=tools, tool_choice="auto")
            resp = _completion_with_temperature_fallback(**kwargs)
        except Exception as e:  # noqa: BLE001
            return {"rc": 2, "out": f"[llm error] {type(e).__name__}: {e}", "turns": turns,
                    "ms": int((time.time() - t0) * 1000)}
        msg = resp.choices[0].message
        am = {"role": "assistant", "content": msg.content or ""}
        tcs = getattr(msg, "tool_calls", None)
        if tcs:
            am["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tcs]
        msgs.append(am)

        if not tcs:
            # 无工具调用 → 这就是答复,返回
            return {"rc": 0, "out": (msg.content or "").strip(), "turns": turns,
                    "ms": int((time.time() - t0) * 1000)}

        for tc in tcs:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            result = dispatch(tc.function.name, args, workdir)
            msgs.append({"role": "tool", "tool_call_id": tc.id,
                         "name": tc.function.name, "content": result})

    # 工具循环到头仍未给文本答复 → 取最后一条 assistant 文本
    last = next((m["content"] for m in reversed(msgs) if m.get("role") == "assistant" and m.get("content")), "")
    return {"rc": 0, "out": last or "[no reply]", "turns": turns, "ms": int((time.time() - t0) * 1000)}


def run_agent(prompt: str, model: str, workdir: str, max_turns: int) -> dict:
    import litellm
    litellm.drop_params = True
    messages = [
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": prompt + f"\n\n[working directory: {workdir}]"},
    ]
    t0 = time.time()
    turns = 0
    for _ in range(max_turns):
        turns += 1
        try:
            resp = _completion_with_temperature_fallback(model=model, messages=messages, tools=TOOLS,
                                      tool_choice="auto", temperature=0)
        except Exception as e:  # noqa: BLE001
            return {"rc": 2, "out": f"[llm error] {type(e).__name__}: {e}", "turns": turns,
                    "ms": int((time.time() - t0) * 1000)}
        msg = resp.choices[0].message
        # record assistant message (with tool_calls if any)
        am = {"role": "assistant", "content": msg.content or ""}
        tcs = getattr(msg, "tool_calls", None)
        if tcs:
            am["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tcs]
        messages.append(am)

        if not tcs:
            text = (msg.content or "").strip()
            if "DONE" in text.upper():
                return {"rc": 0, "out": text, "turns": turns, "ms": int((time.time() - t0) * 1000)}
            # no tool call and not done: nudge once to keep it autonomous
            messages.append({"role": "user", "content":
                             "Continue autonomously. Use tools to act on files. Reply DONE only when all required files exist."})
            continue

        for tc in tcs:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:  # noqa: BLE001
                args = {}
            result = dispatch(tc.function.name, args, workdir)
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "name": tc.function.name, "content": result})

    return {"rc": 1, "out": "[max turns reached]", "turns": turns, "ms": int((time.time() - t0) * 1000)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--message-file")
    ap.add_argument("--message")
    ap.add_argument("--model", default=os.environ.get("SPIKE2_OIAGENT_MODEL", "openrouter/deepseek/deepseek-chat"))
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS_DEFAULT)
    ap.add_argument("--workdir", default=os.getcwd())
    a = ap.parse_args()
    if a.message_file:
        prompt = open(a.message_file, encoding="utf-8").read()
    elif a.message:
        prompt = a.message
    else:
        prompt = sys.stdin.read()
    res = run_agent(prompt, a.model, a.workdir, a.max_turns)
    print(res["out"])
    print(f"\n[oiagent-cli rc={res['rc']} turns={res['turns']} ms={res['ms']}]", file=sys.stderr)
    sys.exit(0 if res["rc"] == 0 else 1)


if __name__ == "__main__":
    main()
