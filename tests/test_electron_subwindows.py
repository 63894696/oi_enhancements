"""
test_electron_subwindows.py — P2.5+21 user 反馈:「语伴/音乐/日程没启动 + 同源工作流死循环」修复验证

bug 1: 语伴 / 音乐 / 日程 3 个子窗后端 Electron 壳从来没 spawn。
        Electron 壳只 spawn 主 web(`prisIragent_web.py`)。Tauri 壳有
        start_companion / start_music,Electron 壳没有对应实现。

bug 2: 同源 window.open 无限递归弹窗 — setWindowOpenHandler 用
        url.startsWith(WEB_URL) allow 同源新窗,任何「关于 / 隐私 / 远程」
        同源 window.open 都触发无限 BrowserWindow 弹出。

bug 3: 工作流子窗 wfmodal fragment 不被处理 — Electron 壳加载
        ${WEB_URL}#wfmodal 时 fragment 永不被监听,主 web 返回主页 HTML,
        wfmodal 永远 display:none。

本测试专注于 bug 2 修法的**纯函数**逻辑:`main.js` 的 `_decideSameOriginOpen`
被抽出成可单独 import 的纯函数,这里用 Node 子进程跑 main.js 验证 JS 真逻辑
(避开 loaders-打包 / Electron ctx 依赖)。

bug 1 + bug 3 因 Electron / Web 端 / async 边,不在此测试(主测试改在
端到端 + 手测)。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SHELL = os.path.join(ROOT, "prisiragent-shell")
MAIN_JS = os.path.join(SHELL, "main.js")

# NTFS case-folding 不影响此文件(main.js 文件名小写)


def _run_node_decide(raw_url: str, web_url: str, recent: dict | None = None) -> dict:
    """调 node 跑 _decideSameOriginOpen 真函数,返 {action, reason?}.

    做法:不 require main.js(它一加载就跑 Electron ctx,污染)。从 main.js 源码
    抽取 _decideSameOriginOpen 函数体 + _RECENT_MS 常量,写到临时 .js 文件,node 跑它。
    这样函数体**真**是 main.js 的代码(不是复制),main.js 改了测试就失效。
    """
    with open(MAIN_JS, "r", encoding="utf-8") as f:
        src = f.read()
    # 抽取 _RECENT_MS const 行
    import re
    m_const = re.search(r"const\s+_RECENT_MS\s*=\s*(\d+)\s*;", src)
    if not m_const:
        raise AssertionError("main.js missing: const _RECENT_MS = ...")
    recent_ms = m_const.group(1)
    # 抽取 _decideSameOriginOpen 函数体(从 function 到匹配大括号结束)
    m_fn = re.search(r"function\s+_decideSameOriginOpen\s*\([^)]*\)\s*\{", src)
    if not m_fn:
        raise AssertionError("main.js missing: function _decideSameOriginOpen")
    start = m_fn.start()
    # 大括号配对
    i = src.index("{", start)
    depth = 1
    j = i + 1
    while j < len(src) and depth > 0:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
        j += 1
    fn_body = src[start:j]
    # 写临时文件
    payload = {
        "url": raw_url,
        "webUrl": web_url,
        "recent": recent or {},
        "now": 1730000000000,
    }
    tmp_js = os.path.join(HERE, "_decide_runner.tmp.js")
    with open(tmp_js, "w", encoding="utf-8") as f:
        f.write(
            "const _RECENT_MS = " + recent_ms + ";\n"
            + fn_body + "\n"
            + f"Date.now = () => {payload['now']};\n"
            + "const out = _decideSameOriginOpen(process.env.P_URL, process.env.P_WEB, JSON.parse(process.env.P_RECENT));\n"
            + "process.stdout.write('RESULT_JSON:' + JSON.stringify(out) + '\\n');\n"
        )
    env = os.environ.copy()
    env["P_URL"] = payload["url"]
    env["P_WEB"] = payload["webUrl"]
    env["P_RECENT"] = json.dumps(payload["recent"])
    node = shutil.which("node") or "node"
    try:
        proc = subprocess.run(
            [node, tmp_js],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=10,
        )
        if proc.returncode != 0:
            raise AssertionError(
                f"node tmp.js failed:\nstdout={proc.stdout[:500]}\nstderr={proc.stderr[:500]}"
            )
        for line in proc.stdout.splitlines():
            if line.startswith("RESULT_JSON:"):
                return json.loads(line[len("RESULT_JSON:"):])
        raise AssertionError(f"no RESULT_JSON line in:\n{proc.stdout[:500]}")
    finally:
        try:
            os.remove(tmp_js)
        except OSError:
            pass


class TestDecideSameOriginOpen(unittest.TestCase):
    """_decideSameOriginOpen 纯函数:同源递归 + 5s debounce。"""

    def test_external_url_returns_external_action(self):
        """外链(非同源)→ action=external(由 caller 走 shell.openExternal)。"""
        out = _run_node_decide(
            "https://github.com/foo/bar",
            "http://127.0.0.1:18802",
            {},
        )
        self.assertEqual(out, {"action": "external"})

    def test_same_origin_first_time_returns_allow(self):
        """同源首次 open → action=allow。"""
        out = _run_node_decide(
            "http://127.0.0.1:18802/prisiragent/about",
            "http://127.0.0.1:18802",
            {},
        )
        self.assertEqual(out, {"action": "allow"})

    def test_same_origin_root_returns_allow(self):
        """同源根 URL(无路径)→ allow。"""
        out = _run_node_decide(
            "http://127.0.0.1:18802",
            "http://127.0.0.1:18802",
            {},
        )
        self.assertEqual(out, {"action": "allow"})

    def test_same_origin_within_5s_returns_deny_debounce(self):
        """同源同 URL 5s 内再次 → deny + reason=debounce。"""
        recent = {"http://127.0.0.1:18802/prisiragent/about": 1730000000000 - 1000}
        out = _run_node_decide(
            "http://127.0.0.1:18802/prisiragent/about",
            "http://127.0.0.1:18802",
            recent,
        )
        self.assertEqual(out.get("action"), "deny")
        self.assertEqual(out.get("reason"), "debounce")

    def test_same_origin_after_5s_returns_allow(self):
        """同源同 URL 5s 后再 open → allow(滑动窗口)。"""
        recent = {"http://127.0.0.1:18802/prisiragent/about": 1730000000000 - 6000}
        out = _run_node_decide(
            "http://127.0.0.1:18802/prisiragent/about",
            "http://127.0.0.1:18802",
            recent,
        )
        self.assertEqual(out, {"action": "allow"})

    def test_different_urls_each_allowed_first_time(self):
        """死循环场景:同窗/多窗反复 open 同一 URL,第二次被 debounce 拦。"""
        # 模拟死循环:主窗 web 端 window.open('/about') → 弹新窗 → 新窗 web
        # 端又触发 handler open('/about') → 应被 debounce 拦。
        url = "http://127.0.0.1:18802/prisiragent/about"
        # 第一次:empty recent → allow
        out1 = _run_node_decide(url, "http://127.0.0.1:18802", {})
        self.assertEqual(out1["action"], "allow")
        # 第二次:recent 里有 100ms 前 → deny
        recent = {url: 1730000000000 - 100}
        out2 = _run_node_decide(url, "http://127.0.0.1:18802", recent)
        self.assertEqual(out2["action"], "deny")
        self.assertEqual(out2["reason"], "debounce")

    def test_different_path_same_origin_both_allowed(self):
        """同源但不同 URL(用户实正常行为)→ 都 allow。"""
        a = "http://127.0.0.1:18802/prisiragent/about"
        b = "http://127.0.0.1:18802/prisiragent/privacy"
        recent = {a: 1730000000000 - 100}  # a 刚 open 过
        # b 没在 recent → allow
        out_b = _run_node_decide(b, "http://127.0.0.1:18802", recent)
        self.assertEqual(out_b["action"], "allow")
        # a 在 recent → debounce
        out_a = _run_node_decide(a, "http://127.0.0.1:18802", recent)
        self.assertEqual(out_a["action"], "deny")

    def test_prefix_collision_not_triggered(self):
        """防 startsWith 误匹:`http://127.0.0.1:188029`(后 1 位差异端口)→ external。"""
        out = _run_node_decide(
            "http://127.0.0.1:188029/foo",  # 不同端口
            "http://127.0.0.1:18802",
            {},
        )
        # 188029 不以 18802+/ 开头 → external(外链或被误开,反正不弹)
        self.assertEqual(out["action"], "external")


class TestMainJsSyntax(unittest.TestCase):
    """main.js 语法编译 sanity(避免 ship 后 electron 启动就崩)。"""

    def test_main_js_parses(self):
        """`node --check` 验 main.js 语法 OK。"""
        node = shutil.which("node") or "node"
        proc = subprocess.run(
            [node, "--check", MAIN_JS],
            cwd=ROOT, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"main.js syntax error:\nstdout={proc.stdout[:500]}\nstderr={proc.stderr[:500]}",
        )

    def test_main_js_has_required_functions(self):
        """验 main.js 含本次 ship 的关键函数/常量。"""
        with open(MAIN_JS, "r", encoding="utf-8") as f:
            content = f.read()
        # helper 函数
        for needle in (
            "function startCompanion()",
            "function startMusic()",
            "function waitForPort(",
            "function _decideSameOriginOpen(",
            "function _pipeProcToLog(",
            "_recentlyOpenedUrls",
            "_RECENT_MS",
        ):
            self.assertIn(needle, content, f"main.js missing: {needle}")
        # 子窗 openCompanionWindow / openMusicWindow 已接 startCompanion/startMusic
        self.assertIn("startCompanion()", content)
        self.assertIn("startMusic()", content)
        # 主窗 + 子窗 setWindowOpenHandler 都用 _decideSameOriginOpen
        self.assertGreaterEqual(content.count("_decideSameOriginOpen"), 2)
        # before-quit 杀 companion/music
        self.assertIn("companionProc.kill()", content)
        self.assertIn("musicProc.kill()", content)
        # calendar-port 已传入 args
        self.assertIn("--calendar-port", content)


class TestPrisirAgentWebWfmodalHash(unittest.TestCase):
    """prisIragent_web.py 加了 hashchange + DOMContentLoaded wfmodal 监听。"""

    def test_prisiragent_web_has_hashchange_handler(self):
        """L8392 附近应含 `location.hash === '#wfmodal'` hashchange 监听。"""
        path = os.path.join(ROOT, "prisIragent_web.py")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for needle in (
            "location.hash === '#wfmodal'",
            "addEventListener('hashchange'",
            "addEventListener('DOMContentLoaded'",
            "__wfModalOpen",
        ):
            self.assertIn(needle, content, f"prisIragent_web.py missing: {needle}")
        # openWorkflow 幂等标记
        self.assertIn("if (window.__wfModalOpen) return;", content)
        # closeWorkflow 清标记
        self.assertIn("window.__wfModalOpen = false;", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)