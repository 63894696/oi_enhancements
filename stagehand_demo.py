"""stagehand act() 真实 GUI 操控 demo

任务:
  1. 打开 wikipedia.org
  2. 在搜索框输入 "Python programming language"
  3. 点击搜索按钮
  4. 提取搜索结果的标题和摘要

验证 stagehand v3.6 act(instruction) + extract(instruction) 真能驱动 Chrome + STEPFUN 做 web task。
"""
import os
import sys
import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# 复用 wrapper 的 path 探测 + env 注入
from browser_use_cli import _stagehand_extract  # noqa

# 写一个 _stagehand_act 函数(extract 的姐妹函数,操作浏览器)
import importlib.util
def _get_stagehand_pkg_path():
    import os as _os
    for p in [
        r"C:\nodejs\lib\node_modules\@browserbasehq\stagehand",
        r"C:\nodejs\node_modules\@browserbasehq\stagehand",
    ]:
        if _os.path.isdir(p) and _os.path.isfile(_os.path.join(p, "package.json")):
            return p
    return None

STAGEHAND_PKG = _get_stagehand_pkg_path()
STAGEHAND_PARENT = os.path.dirname(os.path.dirname(STAGEHAND_PKG)) if STAGEHAND_PKG else None


def _stagehand_run(steps: list, timeout: int = 120) -> dict:
    """跑一连串 stagehand 操作(在同一个 Chrome session 里)

    Args:
        steps: [{"action": "goto"|"act"|"extract"|"observe", ...}, ...]
        timeout: 总超时秒

    Returns:
        {"status": "ok"/"error", "results": [...], "stderr": ...}
    """
    # 把 steps 序列化成 Node 能 import 的 JSON
    steps_json = json.dumps(steps)

    # STEPFUN env
    env = os.environ.copy()
    env["OPENAI_API_KEY"] = os.environ.get("STEPFUN_API_KEY", "")
    env["OPENAI_BASE_URL"] = os.environ.get("STEPFUN_BASE_URL", "")
    env["STEPFUN_API_KEY"] = os.environ.get("STEPFUN_API_KEY", "")
    env["STEPFUN_BASE_URL"] = os.environ.get("STEPFUN_BASE_URL", "")

    # Node 脚本:多步骤 + 输出 JSON
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

    import subprocess
    proc = subprocess.run(
        ["node", "-e", node_script],
        cwd=STAGEHAND_PARENT,
        timeout=timeout,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    # 抽 STEP_RESULT 块
    results = []
    in_block = False
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
# 主流程:真实 GUI 操控 demo
# ============================================================

print("=== stagehand act() 真实 GUI 操控 demo ===")
print("任务:打开 wikipedia → 搜 Python → 提取标题 + 摘要")
print()

steps = [
    # Step 1:goto wikipedia
    {"action": "goto", "url": "https://www.wikipedia.org/"},
    # Step 2:act 在搜索框输入(用英文 instruction 让 stagehand 翻译成 a11y action)
    {"action": "act", "instruction": "Type 'Python programming language' into the search input box"},
    # Step 3:act 点击搜索按钮
    {"action": "act", "instruction": "Click the search button or press Enter to submit the search"},
    # Step 4:observe 看页面有什么元素
    {"action": "observe", "instruction": "main search result heading and snippet"},
    # Step 5:extract 提取内容
    {"action": "extract", "instruction": "main article title and first paragraph summary"},
]

print(f"将执行 {len(steps)} 步,每步预计 5-15s,总耗时 1-3min")
print()

t0 = time.time()
result = _stagehand_run(steps, timeout=300)
elapsed = time.time() - t0

print(f"\n=== 结果 (总耗时 {elapsed:.1f}s) ===")
print(f"status: {result['status']}")
print(f"returncode: {result['returncode']}")
print()
for i, r in enumerate(result["results"], 1):
    print(f"--- Step {i}: {r.get('action', '?')} ---")
    if r.get("action") == "act":
        print(f"  instruction: {r.get('instruction', '')[:60]}...")
        print(f"  success: {r.get('success')}")
        print(f"  message: {r.get('message', '')[:200]}")
        print(f"  actionDescription: {r.get('actionDescription', '')[:200]}")
        print(f"  actions: {r.get('actions', [])}")
    elif r.get("action") == "extract":
        print(f"  instruction: {r.get('instruction', '')[:60]}...")
        print(f"  result: {str(r.get('result', ''))[:400]}")
    elif r.get("action") == "goto":
        print(f"  url: {r.get('url')}")
    elif r.get("action") == "observe":
        acts = r.get("actions", [])
        print(f"  actions count: {len(acts)}")
        for a in acts[:3]:
            print(f"    - {str(a)[:120]}")
    print(f"  elapsed_ms: {r.get('elapsed_ms')}")

print()
if result["stderr_tail"]:
    print(f"stderr (tail):\n{result['stderr_tail'][-300:]}")