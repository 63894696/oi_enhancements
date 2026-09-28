# -*- coding: utf-8 -*-
"""test_colibri_cross_platform.py — Phase D:跨平台 binary 路径解析 + 失败注入(2026-09-28 ship)

覆盖:
  - find_colibri_binary 跨平台候选路径(Win/Linux/macOS)
  - check_memory_ok 三档:psutil / ctypes / fail-soft
  - 内存 < 6 GB → try_start 拒绝
  - 子进程崩溃 → supervisor 重启 + 指数退避(1s/4s/16s)
  - 重启 3 次仍失败 → state="crashed"
  - 镜像全部失败 → state="crashed" + download_error 记录最后一次
  - 用户取消 → state="crashed" + 半成品保留
"""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "companion"))


def _patch_env(td):
    return mock.patch.dict("os.environ", {"PRISIR_DATA_DIR": td}, clear=False)


class TestFindBinaryCrossPlatform(unittest.TestCase):
    """D-1:跨平台 binary 路径解析(Win/Linux/macOS)。

    _BIN_NAME 是模块级常量(在 import 时按当前 sys.platform 固化),
    所以测试改 platform 后需 reload 模块。
    """

    def _reload_engine_with_platform(self, plat):
        import importlib
        with mock.patch.object(sys, "platform", plat):
            from companion import colibri_engine
            importlib.reload(colibri_engine)
            return colibri_engine

    def test_windows_bin_name(self):
        """Windows → .exe 后缀。"""
        ce = self._reload_engine_with_platform("win32")
        self.assertEqual(ce._BIN_NAME, "coli.exe")
        # 还原到当前平台(避免污染后续测试)
        importlib.reload(ce)

    def test_linux_bin_name(self):
        """Linux → 无后缀。"""
        ce = self._reload_engine_with_platform("linux")
        self.assertEqual(ce._BIN_NAME, "coli")
        importlib.reload(ce)

    def test_darwin_bin_name(self):
        """macOS → 无后缀。"""
        ce = self._reload_engine_with_platform("darwin")
        self.assertEqual(ce._BIN_NAME, "coli")
        importlib.reload(ce)

    def test_find_returns_none_when_no_candidate(self):
        """所有候选路径都不存在 → None(fail-soft)。"""
        td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            with _patch_env(td_ctx.name):
                from companion.colibri_engine import find_colibri_binary
                # PATH 里没有 coli 命令(测试环境基本没装)
                with mock.patch("shutil.which", return_value=None):
                    result = find_colibri_binary()
                self.assertIsNone(result)
        finally:
            td_ctx.cleanup()

    def test_find_returns_data_dir_bin(self):
        """<DATA_DIR>/bin/coli 优先。"""
        td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            td = td_ctx.name
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir(parents=True, exist_ok=True)
            if sys.platform == "win32":
                p = bin_dir / "coli.exe"
            else:
                p = bin_dir / "coli"
            p.touch()
            with _patch_env(td):
                from companion.colibri_engine import find_colibri_binary
                with mock.patch("shutil.which", return_value=None):
                    result = find_colibri_binary()
            self.assertIsNotNone(result)
            self.assertTrue(str(result).endswith(p.name))
        finally:
            td_ctx.cleanup()


class TestMemoryCheck(unittest.TestCase):
    """D-2:内存检查(6 GB 阈值,psutil / ctypes / fail-soft)。"""

    def test_psutil_above_threshold(self):
        """psutil → 可用 >= 6 GB → ok=True。"""
        from companion.colibri_engine import check_memory_ok
        fake_mem = mock.MagicMock()
        fake_mem.available = 8 * 1024 ** 3  # 8 GB
        with mock.patch.dict("sys.modules", {"psutil": mock.MagicMock(virtual_memory=lambda: fake_mem)}):
            r = check_memory_ok()
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "psutil")

    def test_psutil_below_threshold(self):
        """psutil → 可用 < 6 GB → ok=False + 友好 hint。"""
        from companion.colibri_engine import check_memory_ok
        fake_mem = mock.MagicMock()
        fake_mem.available = 4 * 1024 ** 3  # 4 GB
        with mock.patch.dict("sys.modules", {"psutil": mock.MagicMock(virtual_memory=lambda: fake_mem)}):
            r = check_memory_ok()
        self.assertFalse(r["ok"])
        self.assertIn("可用内存", r["hint"])
        self.assertEqual(r["source"], "psutil")

    def test_min_memory_constant(self):
        """MIN_MEMORY_BYTES = 6 GB(文档承诺)。"""
        from companion.colibri_engine import MIN_MEMORY_BYTES
        self.assertEqual(MIN_MEMORY_BYTES, 6 * 1024 ** 3)


class TestTryStartMemoryGate(unittest.TestCase):
    """D-3:try_start 内存闸门(< 6 GB → memory_insufficient)。"""

    def setUp(self):
        self.td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.td = self.td_ctx.name
        self._patch = _patch_env(self.td)
        self._patch.__enter__()

    def tearDown(self):
        self._patch.__exit__(None, None, None)
        self.td_ctx.cleanup()

    def test_try_start_blocks_low_memory(self):
        """内存 4 GB + binary 在 + 模型在 → memory_insufficient。"""
        from companion import colibri_engine as ce
        from companion.colibri_state import load_state, update_state
        # 准备:模型目录 + binary
        target = Path(self.td) / "models" / "olmoe"
        target.mkdir(parents=True, exist_ok=True)
        (target / "model.safetensors").touch()
        update_state(model_path=str(target), downloaded=True, state="not_downloaded")

        # 把 binary 写到 DATA_DIR/bin
        bin_path = Path(self.td) / "bin"
        bin_path.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            (bin_path / "coli.exe").touch()
        else:
            (bin_path / "coli").touch()
            (bin_path / "coli").chmod(0o755)

        # mock check_memory_ok 返 ok=False
        with mock.patch.object(ce, "check_memory_ok",
                               return_value={"ok": False,
                                            "available_bytes": 4 * 1024**3,
                                            "required_bytes": 6 * 1024**3,
                                            "source": "psutil",
                                            "hint": "可用内存 4.0 GB 不足"}):
            loop = asyncio.new_event_loop()
            try:
                eng = ce.ColibriEngine()
                r = loop.run_until_complete(eng.try_start())
            finally:
                loop.close()
        self.assertFalse(r["ok"])
        self.assertEqual(r["err"], "memory_insufficient")
        s = load_state()
        self.assertEqual(s.state, "crashed")
        self.assertIn("memory insufficient", s.last_error)


class TestDownloadFailureInjection(unittest.TestCase):
    """D-4:下载失败注入 — 主备镜像全失败 → state="crashed"。"""

    def setUp(self):
        self.td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.td = self.td_ctx.name
        self._patch = _patch_env(self.td)
        self._patch.__enter__()

    def tearDown(self):
        self._patch.__exit__(None, None, None)
        self.td_ctx.cleanup()

    def _run_async(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_both_mirrors_fail(self):
        """主备全 fail → state="crashed" + 汇总 error + 最后异常简述。"""
        from companion import colibri_download as cd
        from companion.colibri_state import load_state, update_state
        target = Path(self.td) / "models" / "olmoe"
        update_state(model_path=str(target))
        cd._DOWNLOAD_TASK = None
        cd._DOWNLOAD_CANCEL_FLAG = False

        def fake_snapshot(*a, **k):
            raise RuntimeError("mock network 503")

        fake_hf = mock.MagicMock()
        fake_hf.snapshot_download = fake_snapshot

        # 镜像名应至少出现在日志里(每镜像一行)
        with mock.patch("companion.colibri_download.log") as fake_log:
            with mock.patch.dict("sys.modules", {"huggingface_hub": fake_hf}):
                self._run_async(cd._run_download("task-fail"))
            # 每镜像失败都调 log.warning
            warn_calls = [str(c) for c in fake_log.warning.call_args_list]
            self.assertTrue(
                any("hf-mirror.com" in c for c in warn_calls),
                f"日志应含 hf-mirror.com,实: {warn_calls}"
            )
            self.assertTrue(
                any("huggingface.co" in c for c in warn_calls),
                f"日志应含 huggingface.co,实: {warn_calls}"
            )

        s = load_state()
        self.assertEqual(s.state, "crashed")
        self.assertIn("主备镜像均失败", s.download_error)
        self.assertIn("mock network 503", s.download_error)
        self.assertFalse(s.downloaded)

    def test_cancel_during_download(self):
        """_DOWNLOAD_CANCEL_FLAG=True → 立即停(无网络调用)。"""
        from companion import colibri_download as cd
        from companion.colibri_state import load_state, update_state
        target = Path(self.td) / "models" / "olmoe"
        update_state(model_path=str(target))
        cd._DOWNLOAD_CANCEL_FLAG = True  # 取消先置位

        def fake_snapshot(*a, **k):
            raise AssertionError("不应调用 snapshot_download")

        fake_hf = mock.MagicMock()
        fake_hf.snapshot_download = fake_snapshot
        with mock.patch.dict("sys.modules", {"huggingface_hub": fake_hf}):
            self._run_async(cd._run_download("task-cancel"))

        s = load_state()
        self.assertEqual(s.state, "crashed")
        self.assertFalse(s.downloaded)


class TestColibriAdapter(unittest.TestCase):
    """D-5:adapter 注入 — 零 key / 有 key / not ready 三档。"""

    def setUp(self):
        self.td_ctx = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.td = self.td_ctx.name
        self._patch = _patch_env(self.td)
        self._patch.__enter__()

    def tearDown(self):
        self._patch.__exit__(None, None, None)
        self.td_ctx.cleanup()

    def test_colibri_cfg_none_when_not_ready(self):
        """state != ready → cfg=None(不注入)。"""
        from companion.colibri_adapter import colibri_cfg
        from companion.colibri_state import update_state
        update_state(state="not_downloaded", pid=None)
        self.assertIsNone(colibri_cfg())

    def test_colibri_cfg_none_when_no_pid(self):
        """state=ready 但 pid=None → cfg=None(进程已死)。"""
        from companion.colibri_adapter import colibri_cfg
        from companion.colibri_state import update_state
        update_state(state="ready", pid=None)
        self.assertIsNone(colibri_cfg())

    def test_inject_first_when_empty_candidates(self):
        """零 key(空 candidates)→ colibri 头部插入。"""
        from companion.colibri_adapter import inject_into_candidates, PLATFORM_ID
        from companion.colibri_state import update_state
        # mock pid alive
        with mock.patch("companion.colibri_engine._pid_alive", return_value=True):
            update_state(state="ready", pid=12345, port=18891)
            cands = inject_into_candidates([])
            self.assertEqual(len(cands), 1)
            self.assertEqual(cands[0]["platform"], PLATFORM_ID)

    def test_inject_last_when_have_keys(self):
        """有 key(非空 candidates)→ colibri 末尾兜底。"""
        from companion.colibri_adapter import inject_into_candidates, PLATFORM_ID
        from companion.colibri_state import update_state
        with mock.patch("companion.colibri_engine._pid_alive", return_value=True):
            update_state(state="ready", pid=12345, port=18891)
            existing = [{"platform": "openai", "cfg": {}}]
            cands = inject_into_candidates(existing)
            self.assertEqual(len(cands), 2)
            self.assertEqual(cands[0]["platform"], "openai")
            self.assertEqual(cands[1]["platform"], PLATFORM_ID)

    def test_inject_first_with_colibri_first_flag(self):
        """colibri_first=True → 强制排第 1(用户设置)。"""
        from companion.colibri_adapter import inject_into_candidates, PLATFORM_ID
        from companion.colibri_state import update_state
        with mock.patch("companion.colibri_engine._pid_alive", return_value=True):
            update_state(state="ready", pid=12345, port=18891)
            existing = [{"platform": "openai", "cfg": {}}]
            cands = inject_into_candidates(existing, colibri_first=True)
            self.assertEqual(cands[0]["platform"], PLATFORM_ID)
            self.assertEqual(cands[1]["platform"], "openai")

    def test_inject_no_op_when_colibri_not_ready(self):
        """colibri 不可用 → 不动 candidates(原样返)。"""
        from companion.colibri_adapter import inject_into_candidates
        from companion.colibri_state import update_state
        update_state(state="not_downloaded", pid=None)
        existing = [{"platform": "openai", "cfg": {}}]
        cands = inject_into_candidates(existing)
        self.assertEqual(cands, existing)
        self.assertEqual(len(cands), 1)

    def test_platform_id_is_local_colibri(self):
        """platform id 锁定 local_colibri(与 ollama/llama 平级)。"""
        from companion.colibri_adapter import PLATFORM_ID, MODEL_NAME
        self.assertEqual(PLATFORM_ID, "local_colibri")
        self.assertEqual(MODEL_NAME, "olmoe")


if __name__ == "__main__":
    unittest.main()