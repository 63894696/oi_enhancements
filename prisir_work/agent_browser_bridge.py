"""agent_browser_bridge.py — vercel-labs/agent-browser 子进程桥
(2026-09-26 ship, P3j T26)。

复现 Vercel Labs agent-browser(~43.2k stars, Native Rust binary)的核心浏览器
交互能力,通过 batch subprocess CLI 模式(每个 CLI 调用走 Rust daemon IPC,
不重 Chromium):

  · 打开 URL / 取 a11y 树 + @refs / 点 / 填 / 跑 JS / 截图 / 关闭

跟 T25 Playwright MCP 的关键差异:
  · 协议:batch subprocess CLI(Rust daemon 自动常驻,subprocess.run 走 IPC)
       vs  T25:stdio JSON-RPC 长连(Node.js 子进程 + MCP 协议)
  · Token:refs 系统 @e1/@e2 跨 snapshot 稳定,~93% token 削减
       vs  T25:每次 snapshot 返全 a11y 树,ref 重排
  · 依赖:npm + Chrome for Testing(~150MB)
       vs  T25:Node.js + npx + Chromium(~200MB)
  · 风险:T26 主线(token 高效);T25 备用(长连常驻)

设计:
  · 复用 gh_bridge._run 模式(单次 subprocess.run + JSON 解析)
  · 不需要常驻子进程,不需要 daemon thread,不需要 queue.Queue
  · 默认 --json 输出(除 close)
  · 健康探测 3 档:missing_cli / not_installed / ready
  · LLM 拿到 snapshot 后用 @eN refs 跨步引用(ab_invalid_ref 处理失效)

公开 API(8 个 fn):
  · ab_health()                          → {ok, mode, version, ...}
  · ab_open(url, *, timeout=30)          → {ok, url, ...}
  · ab_snapshot(*, depth, interactive_only, timeout=30) → {ok, tree, refs}
  · ab_click(ref, *, timeout=30)         → {ok, ref}
  · ab_fill(ref, text, *, submit, timeout=30) → {ok, ref, text}
  · ab_eval(js, *, timeout=30)           → {ok, result}
  · ab_screenshot(filename="", *, full_page, timeout=30) → {ok, path}
  · ab_close()                           → {ok, message}
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

AB_BIN = os.environ.get("AB_BIN", "agent-browser")
AB_TIMEOUT = 30.0
AB_SNAPSHOT_TIMEOUT = 30.0  # snapshot 可能大
# 浏览器引擎默认 chrome-for-testing(agent-browser 默认)
AB_DEFAULT_BROWSER = "chrome"

# agent-browser CLI 安装提示
_INSTALL_HINT = ("agent-browser 未装。\n"
                 "安装命令:npm install -g agent-browser && agent-browser install\n"
                 "(后者会下载 Chrome for Testing 约 150MB)")


# ---------------------------------------------------------------------------
# 内部:跑 agent-browser 子命令
# ---------------------------------------------------------------------------

def _run(args: list[str], *, timeout: float = AB_TIMEOUT,
         need_json: bool = True) -> dict[str, Any]:
    """调 agent-browser 子命令,统一异常处理 + JSON 解析。

    返回值形态:
      成功 + JSON stdout → {ok: True, data: <parsed_dict_or_list>}
      成功 + 非 JSON    → {ok: True, data: {"raw": stdout}}
      失败              → {ok: False, error: "ab_xxx", ...}
    """
    cmd = [AB_BIN, *args]
    if need_json and "--json" not in args:
        # close 类命令不需要 --json;其他命令都强求结构化输出
        cmd.append("--json")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {"ok": False, "installed": False,
                "error": "ab_cli_not_found",
                "hint": _INSTALL_HINT}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "ab_timeout",
                "hint": f"agent-browser 操作超时 ({timeout}s)"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"ab_{type(e).__name__}",
                "detail": str(e)[:200]}
    if proc.returncode != 0:
        stderr = (proc.stderr or "")[-400:]
        return {"ok": False, "error": "ab_failed",
                "returncode": proc.returncode, "stderr": stderr}
    # 解析 stdout JSON
    out = proc.stdout or ""
    try:
        return {"ok": True, "data": json.loads(out)}
    except json.JSONDecodeError:
        return {"ok": True, "data": {"raw": out}}


def _extract_refs(snapshot: Any) -> list[str]:
    """从 snapshot 文本或 JSON 树里抽 @eN refs(给前端 LLM 用)。"""
    refs: list[str] = []
    text = snapshot if isinstance(snapshot, str) else json.dumps(
        snapshot, ensure_ascii=False)
    # 匹配 @e1, @e2, ... 这种短码
    for m in re.finditer(r"@e(\d+)", text):
        refs.append(f"@e{m.group(1)}")
    # 去重但保序
    seen: set[str] = set()
    deduped: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    return deduped


# ---------------------------------------------------------------------------
# 公开 API 1: ab_health(版本探活 + 引擎探活)
# ---------------------------------------------------------------------------

def ab_health(*, timeout: float = 10.0) -> dict[str, Any]:
    """3 档 mode:missing_cli / not_installed / ready。

    检测步骤:
      1. agent-browser 二进制在不在 PATH(否则 missing_cli)
      2. 跑 --version 拿版本号
      3. 跑 doctor 探测 Chrome for Testing 装没装
    """
    bin_path = shutil.which(AB_BIN)
    if not bin_path:
        return {"ok": True, "installed": False, "mode": "missing_cli",
                "bin": "", "version": "", "browser": "",
                "hint": _INSTALL_HINT}
    # 版本
    r = _run(["--version"], timeout=5.0, need_json=False)
    version = ""
    if r.get("ok"):
        data = r.get("data")
        if isinstance(data, dict) and data.get("raw"):
            version = data["raw"].strip().split("\n")[0]
        elif isinstance(data, str):
            version = data.strip().split("\n")[0]
    # doctor(检查 Chrome for Testing / daemon)
    doc = _run(["doctor"], timeout=timeout, need_json=False)
    if not doc.get("ok"):
        return {"ok": True, "installed": True, "mode": "not_installed",
                "bin": bin_path, "version": version,
                "browser": AB_DEFAULT_BROWSER,
                "stderr_tail": doc.get("stderr", "")[-200:],
                "hint": ("agent-browser 已装但 doctor 失败,"
                          "可能 Chrome for Testing 未下载。"
                          "跑 `agent-browser install` 修复")}
    return {"ok": True, "installed": True, "mode": "ready",
            "bin": bin_path, "version": version,
            "browser": AB_DEFAULT_BROWSER, "headless": True}


# ---------------------------------------------------------------------------
# 公开 API 2: ab_open(打开 URL)
# ---------------------------------------------------------------------------

def ab_open(url: str, *, timeout: float = AB_TIMEOUT) -> dict[str, Any]:
    """打开 URL(agent-browser open <url>)。

    成功:{ok: True, url, ...}
    失败:{ok: False, error: "ab_xxx", ...}
    """
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "bad_url",
                "hint": "URL 必须 http(s) 开头"}
    r = _run(["open", url], timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "url": url, **r}
    return {"ok": True, "url": url,
            "data": r.get("data", {}),
            "message": "Opened"}


# ---------------------------------------------------------------------------
# 公开 API 3: ab_snapshot(取 a11y 树 + @refs)
# ---------------------------------------------------------------------------

def ab_snapshot(*, depth: int = 3, interactive_only: bool = False,
                timeout: float = AB_SNAPSHOT_TIMEOUT) -> dict[str, Any]:
    """取 a11y 树(LLM 用来找元素 @ref)。

    返回:{ok: True, tree, refs: [@e1, @e2, ...], raw}
    失败:{ok: False, error: "ab_xxx"}
    """
    d = max(1, min(int(depth), 8))
    args: list[str] = ["snapshot", "-d", str(d)]
    if interactive_only:
        args.append("-i")
    r = _run(args, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, **r}
    data = r.get("data", {})
    if isinstance(data, dict):
        tree_text = (data.get("snapshot") or
                     data.get("tree") or
                     data.get("raw") or
                     json.dumps(data, ensure_ascii=False))
    elif isinstance(data, str):
        tree_text = data
    else:
        tree_text = json.dumps(data, ensure_ascii=False)
    refs = _extract_refs(tree_text)
    return {"ok": True, "tree": tree_text,
            "refs": refs, "raw": data}


# ---------------------------------------------------------------------------
# 公开 API 4: ab_click(点击 @ref)— L1
# ---------------------------------------------------------------------------

def ab_click(ref: str, *, timeout: float = AB_TIMEOUT) -> dict[str, Any]:
    """点 @ref 元素(agent-browser click @eN)。

    ref 必须形如 @e1/@e2/...,否则 bad_ref。
    """
    ref = (ref or "").strip()
    if not ref or not re.match(r"^@e\d+$", ref):
        return {"ok": False, "error": "bad_ref",
                "hint": "ref 必须形如 @e1/@e2/...;先 snapshot 拿最新 refs"}
    r = _run(["click", ref], timeout=timeout)
    if not r.get("ok"):
        # 区分:ref 找不到 vs 别的错
        stderr = r.get("stderr", "") or ""
        if "ref" in stderr.lower() or "not found" in stderr.lower():
            return {"ok": False, "error": "ab_invalid_ref",
                    "ref": ref, "stderr": stderr[-200:],
                    "hint": "@ref 无效(可能 DOM 已变,先重 snapshot)"}
        return {"ok": False, "ref": ref, **r}
    return {"ok": True, "ref": ref, "message": "Clicked"}


# ---------------------------------------------------------------------------
# 公开 API 5: ab_fill(填 @ref 输入框)— L1
# ---------------------------------------------------------------------------

def ab_fill(ref: str, text: str, *,
            submit: bool = False,
            slowly: bool = False,
            timeout: float = AB_TIMEOUT) -> dict[str, Any]:
    """在 @ref 输入框填 text(agent-browser fill @eN "text")。

    submit=True → 填完后按 Enter
    slowly=True → 逐字符慢打(防反爬)
    """
    ref = (ref or "").strip()
    if not ref or not re.match(r"^@e\d+$", ref):
        return {"ok": False, "error": "bad_ref",
                "hint": "ref 必须形如 @e1/@e2/..."}
    if text is None:
        text = ""
    args = ["fill", ref, text]
    if submit:
        args.append("--submit")
    if slowly:
        args.append("--slowly")
    r = _run(args, timeout=timeout)
    if not r.get("ok"):
        stderr = r.get("stderr", "") or ""
        if "ref" in stderr.lower() or "not found" in stderr.lower():
            return {"ok": False, "error": "ab_invalid_ref",
                    "ref": ref, "text": text, "stderr": stderr[-200:]}
        return {"ok": False, "ref": ref, "text": text, **r}
    return {"ok": True, "ref": ref, "text": text,
            "submit": submit, "slowly": slowly,
            "message": "Filled"}


# ---------------------------------------------------------------------------
# 公开 API 6: ab_eval(跑 JS)
# ---------------------------------------------------------------------------

def ab_eval(js: str, *, timeout: float = AB_TIMEOUT) -> dict[str, Any]:
    """在浏览器执行 JS(agent-browser eval 'function')。

    js 必须是函数体字符串,如 '() => document.title'。
    """
    js = (js or "").strip()
    if not js:
        return {"ok": False, "error": "empty_js",
                "hint": "JS 不能为空,如 '() => document.title'"}
    r = _run(["eval", js], timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "js": js[:200], **r}
    data = r.get("data", {})
    # agent-browser eval --json 返 {result: <JSON serializable>}
    result = data.get("result") if isinstance(data, dict) else data
    return {"ok": True, "js": js[:200], "result": result}


# ---------------------------------------------------------------------------
# 公开 API 7: ab_screenshot(截图)
# ---------------------------------------------------------------------------

def ab_screenshot(filename: str = "", *,
                  full_page: bool = False,
                  timeout: float = AB_TIMEOUT) -> dict[str, Any]:
    """截图(agent-browser screenshot [path])。

    filename 留空 → 走默认输出位置(agent-browser 自动生成)。
    full_page=True → 整页(否则可视区域)。
    """
    args: list[str] = ["screenshot"]
    if filename:
        args.append(filename)
    if full_page:
        args.append("--full-page")
    r = _run(args, timeout=timeout)
    if not r.get("ok"):
        return {"ok": False, "filename": filename, **r}
    data = r.get("data", {})
    path = (data.get("path") if isinstance(data, dict) else None) or filename or ""
    return {"ok": True, "filename": filename,
            "path": path, "full_page": full_page}


# ---------------------------------------------------------------------------
# 公开 API 8: ab_close(关闭浏览器)
# ---------------------------------------------------------------------------

def ab_close(*, timeout: float = 10.0) -> dict[str, Any]:
    """关浏览器 + 停 Rust daemon(agent-browser close)。"""
    r = _run(["close"], timeout=timeout, need_json=False)
    if not r.get("ok"):
        # close 失败也尽量返 ok(daemon 可能已死,下次 call 自动重启)
        stderr = r.get("stderr", "") or ""
        return {"ok": True, "message": "Close attempted",
                "warning": r.get("error"), "stderr": stderr[-200:]}
    return {"ok": True, "message": "Browser closed",
            "raw": r.get("data", {}).get("raw", "") if isinstance(
                r.get("data"), dict) else ""}


# ---------------------------------------------------------------------------
# CLI(便于调试)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    import sys as _sys
    cmd = (_sys.argv[1:] or ["health"])[0]
    if cmd == "health":
        print(json.dumps(ab_health(), ensure_ascii=False, indent=2))
    elif cmd == "open":
        url = _sys.argv[2] if len(_sys.argv) > 2 else ""
        print(json.dumps(ab_open(url), ensure_ascii=False, indent=2))
    elif cmd == "snapshot":
        print(json.dumps(ab_snapshot(), ensure_ascii=False, indent=2))
    elif cmd == "click":
        ref = _sys.argv[2] if len(_sys.argv) > 2 else "@e1"
        print(json.dumps(ab_click(ref), ensure_ascii=False, indent=2))
    elif cmd == "close":
        print(json.dumps(ab_close(), ensure_ascii=False, indent=2))
    else:
        print(f"unknown cmd: {cmd}", file=_sys.stderr)
        _sys.exit(2)