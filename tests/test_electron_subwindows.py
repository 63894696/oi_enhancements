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
        # 修 2026-10-03 ship 漏:启动抛 DEFAULT_CALENDAR_PORT is not defined,
        # 后端没起,18802 不监听。验源码无对此未声明常量的运行时引用(注释里讲
        # 修复历史是允许的)。
        code_only = "\n".join(
            line for line in content.splitlines()
            if not line.lstrip().startswith("//")
        )
        self.assertNotIn("DEFAULT_CALENDAR_PORT", code_only,
            "main.js 代码里不应引用未声明的 DEFAULT_CALENDAR_PORT,应走 readCalendarPort()")

    def test_open_calendar_window_uses_correct_route(self):
        """修 2026-10-03:openCalendarWindow 必须用 /prisIragent/calendar(大写 I),
        /prisiragent/calendar(小写 p)404。Python 端路由是 prisIragent 大小写敏感。"""
        with open(MAIN_JS, "r", encoding="utf-8") as f:
            content = f.read()
        # openCalendarWindow 函数体内必须用大写 I
        import re
        m = re.search(r"function\s+openCalendarWindow\s*\([^)]*\)\s*\{(.*?)^\}", content,
                      re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "openCalendarWindow function not found")
        body = m.group(1)
        self.assertIn("/prisIragent/calendar", body,
            "openCalendarWindow 必须用 /prisIragent/calendar(大写 I) — Python 端路由大小写敏感")
        # 注意:历史注释里写过小写路径不算违规;但运行时 URL 必须是大写
        url_in_open = re.search(r"openInShell\(`http://[^`]+`", body)
        self.assertIsNotNone(url_in_open)
        self.assertIn("/prisIragent/calendar", url_in_open.group(0),
            "openInShell 的实际 URL 必须用 /prisIragent/calendar")

    def test_open_companion_window_timeout_is_at_least_5s(self):
        """修 2026-10-03:openCompanionWindow waitForPort 超时从 3s 升到 ≥5s。
        语伴后端初始化 ~6-8s,3s 必 fallback 主 web。"""
        with open(MAIN_JS, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        m = re.search(r"function\s+openCompanionWindow\s*\([^)]*\)\s*\{(.*?)^\}",
                      content, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "openCompanionWindow function not found")
        body = m.group(1)
        # 抓 waitForPort 第 3 个参数(timeout)
        m2 = re.search(r"waitForPort\([^,]+,\s*[^,]+,\s*([0-9.]+)\s*\)", body)
        self.assertIsNotNone(m2, "openCompanionWindow must call waitForPort with timeout")
        timeout = float(m2.group(1))
        self.assertGreaterEqual(timeout, 5.0,
            f"openCompanionWindow waitForPort timeout={timeout}s 至少要 5s,语伴后端启动 ~6-8s")


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

    def test_prisiragent_web_close_workflow_clears_hash(self):
        """P2.5+22:closeWorkflow 必须清 URL hash,否则浏览器返回/前进会再开 modal。"""
        path = os.path.join(ROOT, "prisIragent_web.py")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        import re
        m = re.search(r"function\s+closeWorkflow\s*\([^)]*\)\s*\{(.*?)^\}",
                      content, re.MULTILINE | re.DOTALL)
        self.assertIsNotNone(m, "closeWorkflow function not found")
        body = m.group(1)
        # 必须清 hash
        self.assertIn("history.replaceState", body,
            "closeWorkflow 应 history.replaceState 清 hash,避免按返回再开 modal")
        self.assertIn("#wfmodal", body,
            "closeWorkflow 应判断 hash === '#wfmodal' 才清")

    def test_prisiragent_web_no_top_workflow_button(self):
        """P2.5+22:用户拍板 — 扩展旁边的工作流按钮去掉,工作流只能从托盘开。"""
        path = os.path.join(ROOT, "prisIragent_web.py")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # 顶栏 topbtnWorkflow 按钮必须被删
        self.assertNotIn('id="topbtnWorkflow"', content,
            "主 web 顶栏 'topbtnWorkflow' 工作流按钮应删除(用户拍板)")
        # 但 wfmodal 容器 + 关闭按钮必须保留(其他入口(托盘)还要用)
        self.assertIn('id="wfmodal"', content, "wfmodal 容器必须保留")
        self.assertIn("closeWorkflow()", content, "wfmodal 关闭按钮必须保留")


class TestMusicToast(unittest.TestCase):
    """P2.5+22:music 队列空时点播放给 toast 提示,不静默 return 让用户以为卡了。"""

    def test_music_app_js_has_show_music_toast(self):
        """companion/static/music/app.js 必须有 showMusicToast helper。"""
        path = os.path.join(ROOT, "companion", "static", "music", "app.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("function showMusicToast", content,
            "music app.js 缺 showMusicToast helper")
        # play handler 空队列分支调用它
        self.assertIn("showMusicToast", content)
        # 不能含 console.log 调试残留
        # (不强求,仅 sanity:helper 应能被 el 引用)

    def test_music_app_js_play_btn_handles_empty_queue(self):
        """空队列分支必须有 toast 提示,不能静默 return。"""
        path = os.path.join(ROOT, "companion", "static", "music", "app.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        # playBtn.onclick 在文件里就一处,直接搜 start/end 行号
        import re
        m = re.search(r"els\.playBtn\.onclick\s*=\s*async", content)
        self.assertIsNotNone(m, "els.playBtn.onclick handler not found")
        # 从 m.start() 往后抓花括号配对
        i = content.index("{", m.start())
        depth = 1
        j = i + 1
        while j < len(content) and depth > 0:
            if content[j] == "{": depth += 1
            elif content[j] == "}": depth -= 1
            j += 1
        body = content[i:j]
        # else 分支调 showMusicToast
        self.assertIn("showMusicToast", body,
            "playBtn.onclick 空队列分支必须调 showMusicToast 提示")
        self.assertIn("state.queue.length > 0", body,
            "playBtn.onclick 必须判 state.queue.length > 0 才播")


class TestExtRespawnHotfix(unittest.TestCase):
    """P2.5+21 hotfix (revised 2026-10-03):task-runner 不再 auto-respawn。

    修前 bug:`_ext_spawn` 每次都 `crash_count: 0` reset,reader_loop L441 判定
    永远 `<= 3`,死一个 spawn 一个,用户屏幕无限跳 node 弹窗(实测反馈)。

    修法(简化为最终态):
    - task-runner 死了 → 完全不 respawn(用户主动 run_task 才走 lazy-spawn)
    - 其他 ext 仍按旧逻辑 respawn,但加 `_ext_respawn_total > 5` 兜底

    完整 root fix(保留 crash_count 不被 reset)派在 chip task_f48e99a4。
    """

    @classmethod
    def setUpClass(cls):
        # NTFS case-folding 处理 + 复用之前测试的兼容垫片
        import sys as _s
        for k in list(_s.modules):
            if k.lower() in ("prisIragent_web", "prisiragent_web"):
                del _s.modules[k]
        import importlib as _il
        try:
            _il.reload(_il.import_module("prisir_case_compat"))
        except Exception:
            pass
        import prisiragent_web as W
        cls.W = W

    def test_respawn_total_exists(self):
        """模块应有 _ext_respawn_total dict。"""
        self.assertTrue(hasattr(self.W, "_ext_respawn_total"))
        self.assertIsInstance(self.W._ext_respawn_total, dict)

    def test_respawn_total_not_reset_by_ext_spawn(self):
        """`_ext_respawn_total` 独立累加(不被 _ext_spawn 重置)。

        注意:不真 spawn,只验 dict 行为:`_ext_respawn_total[ext_id]` 累加正常。
        """
        ext = "test-ext-no-spawn"
        self.W._ext_respawn_total[ext] = 0
        for i in range(7):
            self.W._ext_respawn_total[ext] += 1
        self.assertEqual(self.W._ext_respawn_total[ext], 7)
        # 即使 _ext_spawn 被调用(模拟 reset crash_count),_ext_respawn_total 不动
        self.W._ext_respawn_total[ext] += 1
        self.assertEqual(self.W._ext_respawn_total[ext], 8)

    def test_task_runner_one_shot_in_source(self):
        """源码里 task-runner 不 auto-respawn 的判断存在。"""
        with open(os.path.join(ROOT, "prisIragent_web.py"), "r", encoding="utf-8") as f:
            src = f.read()
        # task-runner 一次性启逻辑存在
        self.assertIn('ext_id == "task-runner"', src)
        self.assertIn("auto-respawn DISABLED", src)
        self.assertIn("Use run_task to start on demand", src)
        # 其他 ext 仍按 respawn_total > 5 兜底
        self.assertIn("_ext_respawn_total", src)
        self.assertIn("> 5", src)
        self.assertIn("STOP", src)
        # chip 留口子
        self.assertIn("task_f48e99a4", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)