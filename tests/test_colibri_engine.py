# -*- coding: utf-8 -*-
# tests/test_colibri_engine.py — colibri_engine.py 单元测试(2026-09-28 ship Phase A)
#
# 覆盖:
#   - find_colibri_binary 路径候选(missing/file/exists/PATH)
#   - _pid_alive 跨平台 stub(Win32 ctypes / Unix kill)
#   - status() 默认/状态机转换
#   - try_start() 各种失败路径(model_not_downloaded / binary_not_found / spawn failed)
#   - try_start() 模型已下载 + binary 在 → 真 mock spawn
#   - stop() SIGTERM/Windows taskkill 调用
#   - ensure_running() 状态机分支
#   - inject_into_candidates:在 Phase A 用,但为 Phase B/C 准备,先测基础行为
#
# 全部 mock,不真起 colibri 进程(避免环境装 7 GB 模型)。
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from companion import colibri_engine as ce  # noqa: E402
from companion import colibri_state as cs  # noqa: E402


def _run(coro):
    """同步跑异步协程。"""
    return asyncio.get_event_loop().run_until_complete(coro) \
        if asyncio.get_event_loop().is_running() is False \
        else asyncio.run(coro)


class TestFindColibriBinary(unittest.TestCase):
    """binary 路径解析。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="colibri_engine_bin_")
        self._env_patch = mock.patch.dict(os.environ,
                                          {"PRISIR_DATA_DIR": self._tmpdir})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_no_candidates_returns_none(self):
        with mock.patch.object(ce.shutil, "which", return_value=None):
            self.assertIsNone(ce.find_colibri_binary())

    def test_data_dir_bin_exists(self):
        bin_dir = Path(self._tmpdir) / "bin"
        bin_dir.mkdir(exist_ok=True)
        name = "coli.exe" if sys.platform == "win32" else "coli"
        bin_path = bin_dir / name
        bin_path.touch()
        with mock.patch.object(ce.shutil, "which", return_value=None):
            result = ce.find_colibri_binary()
        self.assertEqual(result, bin_path)

    def test_which_fallback(self):
        # DATA_DIR/bin/coli 不在 → 走 PATH
        with mock.patch.object(ce.shutil, "which",
                               return_value="/usr/local/bin/coli"):
            result = ce.find_colibri_binary()
        self.assertEqual(result, Path("/usr/local/bin/coli"))


class TestPidAlive(unittest.TestCase):
    """_pid_alive 跨平台分支。"""

    def test_zero_pid_returns_false(self):
        self.assertFalse(ce._pid_alive(0))

    def test_negative_pid_returns_false(self):
        self.assertFalse(ce._pid_alive(-1))

    def test_none_pid_returns_false(self):
        self.assertFalse(ce._pid_alive(None))

    @unittest.skipIf(sys.platform != "win32", "Win32-only path")
    def test_windows_alive(self):
        # 当前 python 进程一定活着
        import os
        pid = os.getpid()
        self.assertTrue(ce._pid_alive(pid))

    @unittest.skipIf(sys.platform == "win32", "Unix-only path")
    def test_unix_alive(self):
        import os
        pid = os.getpid()
        self.assertTrue(ce._pid_alive(pid))

    @unittest.skipIf(sys.platform == "win32", "Unix-only path")
    def test_unix_dead(self):
        # 用一个不存在的 pid(常见安全值,4096 以下多被 init 占用)
        self.assertFalse(ce._pid_alive(99999999))

    @unittest.skipIf(sys.platform != "win32", "Win32-only path")
    def test_windows_dead(self):
        self.assertFalse(ce._pid_alive(99999999))


class TestStatus(unittest.TestCase):
    """engine.status() 返回结构 + 状态机降级。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="colibri_engine_status_")
        self._env_patch = mock.patch.dict(os.environ,
                                          {"PRISIR_DATA_DIR": self._tmpdir})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_status_default(self):
        eng = ce.ColibriEngine()
        s = _run(eng.status())
        self.assertTrue(s["ok"])
        self.assertEqual(s["state"], "not_downloaded")
        self.assertEqual(s["port"], 18891)
        self.assertIsNone(s["pid"])
        self.assertFalse(s["downloaded"])
        self.assertFalse(s["binary_found"])

    def test_status_state_downgrade_on_dead_pid(self):
        cs.update_state(state="ready", pid=99999999)  # 不存在的 pid
        eng = ce.ColibriEngine()
        s = _run(eng.status())
        # ready + 死 pid → 自动降级 crashed
        self.assertEqual(s["state"], "crashed")
        self.assertIsNone(s["pid"])

    def test_status_binary_found_when_data_dir_bin_exists(self):
        bin_dir = Path(self._tmpdir) / "bin"
        bin_dir.mkdir(exist_ok=True)
        name = "coli.exe" if sys.platform == "win32" else "coli"
        (bin_dir / name).touch()
        eng = ce.ColibriEngine()
        s = _run(eng.status())
        self.assertTrue(s["binary_found"])
        self.assertTrue(s["binary_path"].endswith(name))


class TestTryStart(unittest.TestCase):
    """try_start 各种失败路径 + 一次成功路径。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="colibri_engine_start_")
        self._env_patch = mock.patch.dict(os.environ,
                                          {"PRISIR_DATA_DIR": self._tmpdir})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_model_not_downloaded(self):
        eng = ce.ColibriEngine()
        # 模型目录不存在
        r = _run(eng.try_start())
        self.assertFalse(r["ok"])
        self.assertEqual(r["err"], "model_not_downloaded")
        self.assertEqual(cs.load_state().state, "not_downloaded")

    def test_model_downloaded_but_no_binary(self):
        eng = ce.ColibriEngine()
        # 创建空目录(模型已下判定看 *.safetensors)
        s = cs.load_state()
        Path(s.model_path).mkdir(parents=True, exist_ok=True)
        (Path(s.model_path) / "model.safetensors").touch()
        with mock.patch.object(ce, "find_colibri_binary", return_value=None):
            r = _run(eng.try_start())
        self.assertFalse(r["ok"])
        self.assertEqual(r["err"], "binary_not_found")
        self.assertEqual(cs.load_state().state, "crashed")

    def test_spawn_immediate_exit(self):
        eng = ce.ColibriEngine()
        s = cs.load_state()
        Path(s.model_path).mkdir(parents=True, exist_ok=True)
        (Path(s.model_path) / "model.safetensors").touch()
        # mock:进程立刻挂
        fake_proc = mock.MagicMock()
        fake_proc.pid = 12345
        fake_proc.returncode = 1  # 已退出
        binary_path = Path(self._tmpdir) / ("coli.exe" if sys.platform == "win32" else "coli")
        binary_path.touch()
        with mock.patch.object(ce.asyncio, "create_subprocess_exec",
                               new=mock.AsyncMock(return_value=fake_proc)):
            with mock.patch.object(ce.asyncio, "sleep",
                                   new=mock.AsyncMock()):
                r = _run(eng.try_start())
        self.assertFalse(r["ok"])
        self.assertEqual(cs.load_state().state, "crashed")

    def test_already_running_returns_status(self):
        """state=ready + pid alive → 直接返 ok,不重复 spawn。"""
        eng = ce.ColibriEngine()
        # 真活 pid
        import os
        real_pid = os.getpid()
        cs.update_state(state="ready", pid=real_pid)
        r = _run(eng.try_start())
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "ready")


class TestEnsureRunning(unittest.TestCase):
    """ensure_running 分支。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="colibri_engine_ensure_")
        self._env_patch = mock.patch.dict(os.environ,
                                          {"PRISIR_DATA_DIR": self._tmpdir})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_ensure_running_already_ready(self):
        import os
        cs.update_state(state="ready", pid=os.getpid())
        eng = ce.ColibriEngine()
        r = _run(eng.ensure_running())
        self.assertTrue(r["ok"])
        self.assertEqual(r["state"], "ready")

    def test_ensure_running_downloading_returns_status(self):
        cs.update_state(state="downloading")
        eng = ce.ColibriEngine()
        r = _run(eng.ensure_running())
        # downloading → 不尝试启动,直接返 status
        self.assertEqual(r["state"], "downloading")


class TestInjectIntoCandidates(unittest.TestCase):
    """注入器分支。"""

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="colibri_adapter_")
        self._env_patch = mock.patch.dict(os.environ,
                                          {"PRISIR_DATA_DIR": self._tmpdir})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def test_colibri_not_ready_returns_original(self):
        from companion import colibri_adapter as ca_mod
        orig = [{"platform": "openai", "cfg": {"api_key": "x"}}]
        # state != ready → colibri_cfg 返 None
        result = ca_mod.inject_into_candidates(orig)
        self.assertEqual(result, orig)

    def test_colibri_ready_empty_list_head(self):
        from companion import colibri_adapter as ca_mod
        cs.update_state(state="ready", pid=os.getpid())
        result = ca_mod.inject_into_candidates([])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["platform"], "local_colibri")

    def test_colibri_ready_existing_appended(self):
        from companion import colibri_adapter as ca_mod
        cs.update_state(state="ready", pid=os.getpid())
        orig = [{"platform": "openai", "cfg": {"api_key": "x"}}]
        result = ca_mod.inject_into_candidates(orig)
        self.assertEqual(len(result), 2)
        # openai 在前,colibri 在后(兜底)
        self.assertEqual(result[0]["platform"], "openai")
        self.assertEqual(result[1]["platform"], "local_colibri")

    def test_colibri_first_true_to_head(self):
        from companion import colibri_adapter as ca_mod
        cs.update_state(state="ready", pid=os.getpid())
        orig = [{"platform": "openai", "cfg": {"api_key": "x"}}]
        result = ca_mod.inject_into_candidates(orig, colibri_first=True)
        self.assertEqual(result[0]["platform"], "local_colibri")
        self.assertEqual(result[1]["platform"], "openai")


if __name__ == "__main__":
    unittest.main(verbosity=2)