# -*- coding: utf-8 -*-
"""P2.5+11 E2E:Electron 启动链路端口解析三段优先级

测试目标(2026-09-20):
  A. HKCU web_port 写 18888 → port_config.readWebPort() 期望 18888(优先级 1)
  B. HKCU 删 + JSON ports.json[web]=18889 → 期望 18889(优先级 2)
  C. HKCU/JSON 都清 → 期望 18802 默认(优先级 3)
  D. env 优先(脚本化模拟 main.js env 覆盖逻辑)
  E. Sentinel 行级 scan(分包到达也能命中)
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHELL_DIR = os.path.join(ROOT, "prisiragent-shell")


def expect(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


def run_node(code: str, env: dict | None = None, timeout: int = 10) -> dict:
    """调 node -e 跑 JS 代码,返 {returncode, stdout, stderr}"""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    r = subprocess.run(
        ["node", "-e", code],
        capture_output=True, text=True, timeout=timeout,
        env=full_env,
        cwd=SHELL_DIR,
    )
    return {
        "returncode": r.returncode,
        "stdout": r.stdout,
        "stderr": r.stderr,
    }


def clear_test_registry() -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["reg", "delete", r"HKCU\Software\PrisirAI", "/f"],
        capture_output=True, timeout=5,
    )


def write_test_registry(port: int) -> None:
    if sys.platform != "win32":
        return
    subprocess.run(
        ["reg", "add", r"HKCU\Software\PrisirAI", "/v", "web_port",
         "/t", "REG_DWORD", "/d", str(port), "/f"],
        capture_output=True, timeout=5,
    )


NODE_HARNESS = """
const port_config = require("./port_config");

function clearTestRegistry(cb) {
  if (process.platform !== "win32") return cb();
  const { execSync } = require("child_process");
  try { execSync('reg delete "HKCU\\\\Software\\\\PrisirAI" /f', { stdio: "ignore", windowsHide: true }); } catch (_) {}
  cb();
}

function writeTestRegistry(port, cb) {
  if (process.platform !== "win32") return cb();
  const { execSync } = require("child_process");
  execSync(`reg add "HKCU\\\\Software\\\\PrisirAI" /v web_port /t REG_DWORD /d ${port} /f`,
    { stdio: "ignore", windowsHide: true });
  cb();
}

const results = {};

function stepA() {
  return new Promise((resolve) => {
    clearTestRegistry(() => {
      writeTestRegistry(18888, () => {
        results.A = port_config.readWebPort();
        clearTestRegistry(resolve);
      });
    });
  });
}

function stepB() {
  return new Promise((resolve) => {
    port_config.writeJson("web", 18889);
    results.B = port_config.readWebPort();
    port_config.writeJson("web", null);
    resolve();
  });
}

function stepB_invalid() {
  return new Promise((resolve) => {
    // 写一个越界值 99999,期望 readWebPort 忽略并回退默认
    const paths = port_config._jsonPathCandidates();
    for (const p of paths) {
      try {
        const fs = require("fs");
        const path = require("path");
        const dir = path.dirname(p);
        fs.mkdirSync(dir, { recursive: true });
        let data = {};
        try { data = JSON.parse(fs.readFileSync(p, "utf8") || "{}"); } catch (_) {}
        data.web = 99999;  // 越界
        fs.writeFileSync(p, JSON.stringify(data));
        break;
      } catch (_) {}
    }
    results.B_invalid = port_config.readWebPort();
    // 清掉
    port_config.writeJson("web", null);
    resolve();
  });
}

function stepC() {
  return new Promise((resolve) => {
    clearTestRegistry(() => {
      port_config.writeJson("web", null);
      results.C = port_config.readWebPort();
      resolve();
    });
  });
}

// Sentinel parse: 复用 main.js 同款 BufRead 算法
const SENTINEL_RE = /\\[prisIragent_web\\] PRISIR_WEB_READY port=(\\d+)/;
function parseSentinel(chunks) {
  let buf = "";
  for (const c of chunks) {
    buf += c;
    let nl;
    while ((nl = buf.indexOf("\\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      const m = line.match(SENTINEL_RE);
      if (m) return parseInt(m[1], 10);
    }
  }
  return null;
}

(async () => {
  await stepA();
  await stepB();
  await stepB_invalid();
  await stepC();
  // E. sentinel
  results.E_split = parseSentinel([
    "[prisIragent_web] Listening on http://127.0.0.1:18803\\n",
    "[prisIragent_web] PRISIR_WEB_READY port=",
    "18803\\n",
    "[prisIragent_web] startup host=127.0.0.1 port=18803\\n"
  ]);
  results.E_same = parseSentinel(["[prisIragent_web] PRISIR_WEB_READY port=18802\\n"]);
  results.E_none = parseSentinel([
    "[prisIragent_web] Listening on http://127.0.0.1:18802\\n",
    "[prisIragent_web] startup host=127.0.0.1 port=18802\\n",
    "some other log line\\n"
  ]);
  // D. env 优先:脚本化模拟 main.js 行为
  // 写 HKCU 一个端口,然后让 env 给出另一个,期望 env 赢
  const envPort = 18900;
  const { execSync } = require("child_process");
  try {
    execSync('reg add "HKCU\\\\Software\\\\PrisirAI" /v web_port /t REG_DWORD /d 18080 /f',
      { stdio: "ignore", windowsHide: true });
  } catch (_) {}
  const _env = process.env.PRISIRAGENT_WEB_PORT || process.env.OIAGENT_WEB_PORT || "";
  const _envPort = parseInt(_env, 10);
  results.D_effective = (_envPort >= 1 && _envPort <= 65535)
    ? _envPort
    : port_config.readWebPort();
  // 清掉
  try { execSync('reg delete "HKCU\\\\Software\\\\PrisirAI" /f', { stdio: "ignore", windowsHide: true }); } catch (_) {}
  port_config.writeJson("web", null);
  console.log(JSON.stringify(results));
})();
"""


def main() -> None:
    print("=== P2.5+11 E2E:Electron 端口解析 ===")
    r = run_node(NODE_HARNESS, env={"PRISIRAGENT_WEB_PORT": "18900"})
    if r["returncode"] != 0:
        print("NODE STDOUT:", r["stdout"])
        print("NODE STDERR:", r["stderr"])
        sys.exit(1)
    try:
        results = json.loads(r["stdout"].strip())
    except json.JSONDecodeError as e:
        print("PARSE FAIL:", e)
        print("RAW:", r["stdout"])
        sys.exit(1)

    # ---- A. HKCU 优先级最高 ----
    print("A. HKCU web_port 优先级最高:")
    expect(f"readWebPort() → 18888", results["A"] == 18888, f"got={results['A']}")

    # ---- B. JSON fallback ----
    print("B. JSON fallback 优先级:")
    expect(f"writeJson(18889) → readWebPort() = 18889",
           results["B"] == 18889, f"got={results['B']}")
    expect(f"JSON web=99999(越界)→ 回退默认 18802",
           results["B_invalid"] == 18802, f"got={results['B_invalid']}")

    # ---- C. 都清 → 默认 18802 ----
    print("C. HKCU/JSON 都清 → 默认 18802:")
    expect(f"readWebPort() → 18802", results["C"] == 18802, f"got={results['C']}")

    # ---- D. env 优先 ----
    print("D. env 优先级最高:")
    expect(f"env=18900 覆盖 HKCU=18080 → effective=18900",
           results["D_effective"] == 18900, f"got={results['D_effective']}")

    # ---- E. Sentinel ----
    print("E. Sentinel 行级 scan:")
    expect(f"split chunks → 18803(分包到达也能命中)",
           results["E_split"] == 18803, f"got={results['E_split']}")
    expect(f"同端口 sentinel → 18802",
           results["E_same"] == 18802, f"got={results['E_same']}")
    expect(f"无 sentinel → null",
           results["E_none"] is None, f"got={results['E_none']}")

    print("\n=== P2.5+11 E2E ALL PASS ===")


if __name__ == "__main__":
    main()