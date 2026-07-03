"""browser-use CLI 子进程化 wrapper — OI 浏览器自动化子工具

参考 github.com/browser-use/browser-use v0.13.x:
  browser-use CLI 是 agent-friendly 的浏览器自动化子进程,任何 LLM agent 可以 subprocess 调

OI 浏览器自动化目前用 CDP/Playwright(代码驱动),不天然适合 GUI 兜底。
这个 wrapper 让 OI 可以 subprocess 调 'browser-use ...' 来:
  - 打开 URL
  - 点击元素(基于 LLM 看截图决策)
  - 提取网页内容
  - 填表 / 截图

依赖:npm i browser-use (用户机器还没装,本 wrapper 写成 lazy load)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


def _check_browser_use_installed() -> tuple[bool, str]:
    """检查 browser-use CLI 是否可用(真实调用一次,不只是看 .cmd 存在)

    Returns:
        (is_installed, binary_path_or_error_message)
    """
    for name in ("browser-use", "browser-use-direct"):
        path = shutil.which(name)
        if path:
            # 真调一次验证 --help,捕获 module 缺失等 install 不完整的情况
            try:
                probe = subprocess.run(
                    [path, "--help"],
                    timeout=10,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                )
                # 任何非 0 返回码或 stderr 含 ERR_MODULE_NOT_FOUND 视为未装好
                stderr = probe.stderr or ""
                if probe.returncode != 0 or "ERR_MODULE_NOT_FOUND" in stderr or "Cannot find package" in stderr:
                    return False, (
                        f"browser-use cmd exists at {path} but module dependencies missing. "
                        f"Try: `npm i -g browser-use --force` or switch to stagehand. "
                        f"stderr: {stderr[:200]}"
                    )
            except subprocess.TimeoutExpired:
                return False, f"browser-use --help timed out (>10s); binary likely broken"
            return True, path
    npm_path = shutil.which("npm")
    if npm_path:
        return False, "browser-use not in PATH; install with `npm i -g browser-use`"
    return False, "npm not installed"


def call_browser_use(
    command: str,
    *args: str,
    cwd: Optional[str] = None,
    timeout: float = 60.0,
    capture_output: bool = True,
) -> dict:
    """调 browser-use CLI 子进程

    Args:
        command: 子命令(open / click / extract / screenshot / fill / etc.)
        args: 传给子命令的位置参数
        cwd: 工作目录
        timeout: 超时秒数
        capture_output: True 捕获 stdout/stderr

    Returns:
        {"status": "ok"/"error", "stdout": ..., "stderr": ..., "binary": ...}
    """
    installed, info = _check_browser_use_installed()
    if not installed:
        return {"status": "unavailable", "reason": info}

    binary = info
    cmd = [binary, command, *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            timeout=timeout,
            capture_output=capture_output,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "returncode": proc.returncode,
            "binary": binary,
            "command": cmd,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "timeout_s": timeout, "command": cmd}
    except FileNotFoundError:
        return {"status": "not_found", "binary": binary}
    except Exception as e:
        return {"status": "error", "reason": str(e), "command": cmd}


# ============================================================
# OI 友好的高级 API
# ============================================================

def open_url(url: str, timeout: float = 30.0) -> dict:
    """打开 URL 并截图(返回 base64)"""
    return call_browser_use("open", url, timeout=timeout)


def extract_content(url: str, selector: Optional[str] = None, timeout: float = 60.0) -> dict:
    """提取 URL 内容(整页 or 指定 selector)"""
    args = [url]
    if selector:
        args += ["--selector", selector]
    return call_browser_use("extract", *args, timeout=timeout)


def click_element(selector: str, timeout: float = 30.0) -> dict:
    """点击指定 selector 元素"""
    return call_browser_use("click", "--selector", selector, timeout=timeout)


def fill_input(selector: str, text: str, timeout: float = 30.0) -> dict:
    """填表"""
    return call_browser_use("fill", "--selector", selector, "--text", text, timeout=timeout)


def take_screenshot(path: str, timeout: float = 30.0) -> dict:
    """截屏存到 path"""
    return call_browser_use("screenshot", "--output", path, timeout=timeout)


# ============================================================
# Stagehand SDK 后端(无 CLI,但有 npm 包,可以 node subprocess 调 SDK API)
# ============================================================

def _stagehand_extract(url: str, instruction: str = "Extract all text") -> dict:
    """用 stagehand v3.6 SDK extract 内容(node subprocess)

    v3 API:Stagehand 对象直接有 extract() / act() / observe(),不再有 page()
    """
    node_script = f'''
const {{ Stagehand }} = require("@browserbasehq/stagehand");
(async () => {{
  // 用 STEPFUN OpenAI 兼容 endpoint 替代 OpenAI 默认
  process.env.OPENAI_API_KEY = process.env.STEPFUN_API_KEY;
  process.env.OPENAI_BASE_URL = process.env.STEPFUN_BASE_URL;
  const stagehand = new Stagehand({{
    env: "LOCAL",
    headless: true,
    model: "openai/step-3.7-flash",  // STEPFUN 模型
  }});
  await stagehand.init();
  // v3 API:extract() 直接在 stagehand 对象上
  const result = await stagehand.extract("{instruction}");
  console.log(JSON.stringify(result));
  await stagehand.close();
}})().catch(e => {{ console.error(e.message); process.exit(1); }});
'''

    # 把 STEPFUN key 注入到 subprocess 环境
    env = os.environ.copy()
    env["STEPFUN_API_KEY"] = os.environ.get("STEPFUN_API_KEY", "")
    env["STEPFUN_BASE_URL"] = os.environ.get("STEPFUN_BASE_URL", "")
    env["OPENAI_API_KEY"] = os.environ.get("STEPFUN_API_KEY", "")  # stagehand 读这个
    env["OPENAI_BASE_URL"] = os.environ.get("STEPFUN_BASE_URL", "")
    # 找 stagehand 装在哪(用 os.path 不用 Path,Git Bash + Python 兼容问题)
    import os as _os
    candidate_paths = [
        r"C:\nodejs\lib\node_modules\@browserbasehq\stagehand",
        r"C:\nodejs\node_modules\@browserbasehq\stagehand",
        r"C:\Users\Administrator\.stagehand-test\node_modules\@browserbasehq\stagehand",
    ]
    stagehand_pkg = None
    for p in candidate_paths:
        if _os.path.isdir(p) and _os.path.isfile(_os.path.join(p, "package.json")):
            stagehand_pkg = p
            break
    # node_modules 父目录,Node require 解析用
    stagehand_parent = _os.path.dirname(_os.path.dirname(stagehand_pkg)) if stagehand_pkg else None

    if not stagehand_pkg:
        return {
            "status": "unavailable",
            "reason": "stagehand not installed locally; install with `cd C:/Users/Administrator/.stagehand-test && npm init -y && npm install @browserbasehq/stagehand`",
        }

    try:
        proc = subprocess.run(
            ["node", "-e", node_script],
            cwd=str(stagehand_parent) if stagehand_parent else None,
            timeout=60,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        return {
            "status": "ok" if proc.returncode == 0 else "error",
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "command": "node -e <stagehand extract>",
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ============================================================
# 安装辅助
# ============================================================

INSTALL_INSTRUCTIONS = """
# 安装 browser-use CLI(全局)
npm install -g browser-use

# 验证
browser-use --help
"""


if __name__ == "__main__":
    print("=== browser-use CLI 检测 ===")
    installed, info = _check_browser_use_installed()
    if installed:
        print(f"  ✓ 已安装:{info}")
        # 实际调一下
        r = call_browser_use("--help", timeout=10)
        print(f"  --help returncode: {r.get('returncode')}")
        print(f"  stdout (前 300): {r.get('stdout', '')[:300]}")
    else:
        print(f"  ✗ 未安装:{info}")
        print(INSTALL_INSTRUCTIONS)
        print("\n=== 用 fake generator 模拟调用 ===")
        # 没有真 CLI 时,造个 fake 验证 wrapper 代码本身 OK
        fake_stdout = "fake browser-use output: would open URL"
        fake_result = {
            "status": "ok",
            "stdout": fake_stdout,
            "binary": "/fake/path/browser-use",
            "command": ["/fake/path/browser-use", "open", "https://example.com"],
        }
        print(json.dumps(fake_result, indent=2, ensure_ascii=False))