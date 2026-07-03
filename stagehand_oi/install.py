"""stagehand → OI agent 集成 installer

把 stagehand v3.6 + STEPFUN 包装成 OI interpreter 的工具 + system prompt 描述,
让 OI 能自动调用浏览器做 web task(搜索 / 点击 / 提取)。

用法:
    from interpreter import interpreter
    from stagehand_oi.install import install_stagehand_tools
    install_stagehand_tools(interpreter)

之后 OI 会自动用 stagehand 处理 web task。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# stagehand 路径探测(用 os.path 不用 pathlib 兼容 Git Bash)
import os as _os
_STAGEHAND_PKG = None
for _p in [
    r"C:\nodejs\lib\node_modules\@browserbasehq\stagehand",
    r"C:\nodejs\node_modules\@browserbasehq\stagehand",
]:
    if _os.path.isdir(_p) and _os.path.isfile(_os.path.join(_p, "package.json")):
        _STAGEHAND_PKG = _p
        break
_STAGEHAND_PARENT = _os.path.dirname(_os.path.dirname(_STAGEHAND_PKG)) if _STAGEHAND_PKG else None


def _check_stagehand_available() -> dict:
    """检查 stagehand 是否可用 + STEPFUN key 是否配"""
    if not _STAGEHAND_PKG:
        return {"available": False, "reason": "stagehand not installed"}
    stepfun_key = _os.environ.get("STEPFUN_API_KEY", "")
    if not stepfun_key:
        return {"available": False, "reason": "STEPFUN_API_KEY not in env"}
    return {"available": True, "stagehand_path": _STAGEHAND_PKG}


# ============================================================
# Stagehand 多步骤执行器(给 OI 用)
# ============================================================

def stagehand_run(steps: list, timeout: int = 300) -> dict:
    """跑一连串 stagehand 操作(在同一个 Chrome session 里)

    Args:
        steps: [{"action": "goto"|"act"|"extract"|"observe", ...}, ...]
        timeout: 总超时秒(默认 5 分钟)

    Returns:
        {"status": "ok"/"error", "results": [...], "stderr": "..."}
    """
    avail = _check_stagehand_available()
    if not avail["available"]:
        return {"status": "unavailable", "reason": avail["reason"]}

    steps_json = json.dumps(steps)

    # STEPFUN env(stagehand AI SDK 读 OPENAI_*)
    env = _os.environ.copy()
    env["OPENAI_API_KEY"] = _os.environ.get("STEPFUN_API_KEY", "")
    env["OPENAI_BASE_URL"] = _os.environ.get("STEPFUN_BASE_URL", "")

    node_script = '''
const { Stagehand } = require("@browserbasehq/stagehand");

const steps = __STEPS_JSON__;

(async () => {
  const stagehand = new Stagehand({
    env: "LOCAL",
    headless: true,
    model: "openai/step-3.7-flash",
  });
  await stagehand.init();
  const results = [];
  try {
    for (const step of steps) {
      const t0 = Date.now();
      let res;
      if (step.action === "goto") {
        await stagehand.context.activePage().goto(step.url);
        res = { action: "goto", url: step.url };
      } else if (step.action === "act") {
        const r = await stagehand.act(step.instruction);
        res = {
          action: "act",
          instruction: step.instruction,
          success: r.success,
          message: r.message,
          actionDescription: r.actionDescription,
          actions: r.actions,
        };
      } else if (step.action === "extract") {
        const r = await stagehand.extract(step.instruction);
        res = { action: "extract", instruction: step.instruction, result: r };
      } else if (step.action === "observe") {
        const r = await stagehand.observe(step.instruction || undefined);
        res = { action: "observe", actions: r };
      } else {
        res = { action: "unknown", step };
      }
      res.elapsed_ms = Date.now() - t0;
      results.push(res);
      console.log("STEP_RESULT_BEGIN");
      console.log(JSON.stringify(res));
      console.log("STEP_RESULT_END");
    }
  } catch (e) {
    console.error("ERR:", e.message, e.stack);
    process.exit(2);
  } finally {
    await stagehand.close();
  }
  console.log("ALL_DONE");
  process.exit(0);
})();
'''.replace("__STEPS_JSON__", steps_json)

    proc = subprocess.run(
        ["node", "-e", node_script],
        cwd=_STAGEHAND_PARENT,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    results = []
    in_block = False
    buf = []
    for line in (proc.stdout or "").splitlines():
        if line.strip() == "STEP_RESULT_BEGIN":
            in_block = True
            buf = []
            continue
        if line.strip() == "STEP_RESULT_END":
            in_block = False
            try:
                results.append(json.loads("\n".join(buf)))
            except Exception:
                pass
            continue
        if in_block:
            buf.append(line)

    return {
        "status": "ok" if proc.returncode == 0 else "error",
        "returncode": proc.returncode,
        "results": results,
        "stderr_tail": (proc.stderr or "")[-500:],
    }


# ============================================================
# 装到 OI interpreter
# ============================================================

# 工具描述(OI LLM 看到这个 system prompt 才知道怎么调)
TOOL_DESCRIPTOR = """

# 工具:stagehand 浏览器自动化

当你需要做 web task(打开网页 / 搜索 / 点击 / 提取信息 / 填表)时,
调用 `stagehand_run(steps)` 工具。

## 可用 step action:

1. **goto** — 打开 URL
   ```json
   {"action": "goto", "url": "https://..."}
   ```

2. **act** — 自然语言指令驱动浏览器(点击 / 输入 / 提交)
   ```json
   {"action": "act", "instruction": "Click the search input box then type 'Python tutorial'"}
   ```
   **instruction 越具体越好**,不要写"click search",写"click the magnifying glass icon in the top right corner"

3. **extract** — 提取结构化或非结构化内容
   ```json
   {"action": "extract", "instruction": "main article title and first paragraph summary"}
   ```

4. **observe** — 列出当前页面可操作元素(用于规划)
   ```json
   {"action": "observe", "instruction": "all clickable buttons and input fields"}
   ```

## 使用流程:

1. **observe** 探索页面有什么元素
2. 规划 **steps**(goto → act → extract 链)
3. **stagehand_run(steps)** 一次执行
4. 从 results 里读 extract 的 result 字段作为答案

## 限制:

- 每次 stagehand_run 是一个**完整 session**(从 init 到 close,大约 30-60s 开销)
- **act 失败率高**,instruction 要非常精确
- 中文 Wikipedia / 中文网站也能识别,但 instruction 用英文更稳
- 头无 GUI 模式(headless),真实浏览器窗口不显示
"""


def install_stagehand_tools(interpreter) -> dict:
    """把 stagehand 工具装到 OI interpreter

    Returns:
        {"installed": bool, "reason": str}
    """
    avail = _check_stagehand_available()
    if not avail["available"]:
        return {"installed": False, "reason": avail["reason"]}

    # 1. 给 interpreter 加一个 method(让 OI 能调)
    interpreter.stagehand_run = stagehand_run
    interpreter._stagehand_available = True

    # 2. 注入 system prompt 描述
    if hasattr(interpreter, 'system_message'):
        current = interpreter.system_message or ""
        if TOOL_DESCRIPTOR not in current:
            interpreter.system_message = current + TOOL_DESCRIPTOR
    # 0.4.3 用 .system_message,有些版本用 .system
    elif hasattr(interpreter, 'system'):
        current = interpreter.system or ""
        if TOOL_DESCRIPTOR not in current:
            interpreter.system = current + TOOL_DESCRIPTOR

    return {"installed": True, "stagehand_path": _STAGEHAND_PKG}


# ============================================================
# 烟测
# ============================================================

if __name__ == "__main__":
    import time

    print("=== install 烟测 ===")
    # 模拟 interpreter
    class FakeInterpreter:
        system_message = "You are OI agent."

    fake = FakeInterpreter()
    result = install_stagehand_tools(fake)
    print(f"install result: {result}")
    print(f"system_message after install:\n{fake.system_message[:300]}...")
    print()

    print("=== 真 stagehand_run smoke ===")
    print("(打开 wikipedia → 搜索框输入 → 点击搜索按钮 → 提取标题)")
    steps = [
        {"action": "goto", "url": "https://www.wikipedia.org/"},
        {"action": "act", "instruction": "Click on the search input box at the top center of the page"},
        {"action": "act", "instruction": "Type the word 'Python' into the now-focused search input"},
        {"action": "act", "instruction": "Click the search button to submit the search"},
        {"action": "extract", "instruction": "main article title"},
    ]
    t0 = time.time()
    r = stagehand_run(steps, timeout=300)
    elapsed = time.time() - t0
    print(f"\n总耗时 {elapsed:.1f}s, status={r['status']}, returncode={r.get('returncode')}")
    print(f"results count: {len(r.get('results', []))}")
    for i, sr in enumerate(r.get('results', []), 1):
        print(f"  Step {i} ({sr.get('action')}): elapsed={sr.get('elapsed_ms')}ms")
        if sr.get('action') == 'act':
            print(f"    success={sr.get('success')}, message={sr.get('message', '')[:80]}")
        elif sr.get('action') == 'extract':
            print(f"    result={str(sr.get('result', ''))[:120]}")
    if r.get('stderr_tail'):
        print(f"\nstderr tail:\n{r['stderr_tail'][-300:]}")