# -*- coding: utf-8 -*-
# tests/test_colibri_state.py — colibri_state.py 单元测试(2026-09-28 ship Phase A)
#
# 覆盖:
#   - 默认值生成
#   - 文件不存在 → 兜底默认
#   - 文件存在 + 字段缺失 → 兜底默认 + 保留旧字段
#   - 文件损坏 → 兜底默认
#   - 文件合法 → 完整读回
#   - 写入 + 再读一致性
#   - update_state 原子操作
#   - is_model_path_set 目录存在/不存在/空目录/有文件
#   - state_path 路径正确
#
# 用 unittest + tmpdir(env override)+ 自带 dataclass 断言。
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# 让 tests/ 能 import companion/*
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))

from companion import colibri_state as cs  # noqa: E402


class TestColibriState(unittest.TestCase):

    def setUp(self) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="colibri_state_test_")
        self._env_patch = mock.patch.dict(os.environ,
                                          {"PRISIR_DATA_DIR": self._tmpdir})
        self._env_patch.start()

    def tearDown(self) -> None:
        self._env_patch.stop()
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    # ----- load_state 默认 -----
    def test_load_no_file_returns_defaults(self):
        s = cs.load_state()
        self.assertFalse(s.downloaded)
        self.assertEqual(s.state, "not_downloaded")
        self.assertEqual(s.port, cs.DEFAULT_PORT)
        self.assertEqual(s.port, 18891)
        self.assertIsNone(s.pid)
        self.assertFalse(s.dismissed)
        # model_path 自动填默认
        self.assertTrue(s.model_path.replace("\\", "/").endswith("models/olmoe"))
        self.assertIn(self._tmpdir.replace("\\", "/"), s.model_path.replace("\\", "/"))

    # ----- 损坏文件 -----
    def test_load_garbage_returns_defaults(self):
        p = cs.state_path()
        p.write_text("{not json", encoding="utf-8")
        s = cs.load_state()
        self.assertEqual(s.state, "not_downloaded")
        self.assertFalse(s.dismissed)

    def test_load_wrong_type_returns_defaults(self):
        p = cs.state_path()
        p.write_text("[]", encoding="utf-8")
        s = cs.load_state()
        self.assertEqual(s.state, "not_downloaded")

    # ----- 缺字段兜底 -----
    def test_load_partial_fields_fills_defaults(self):
        p = cs.state_path()
        p.write_text(json.dumps({"downloaded": True, "pid": 12345}),
                     encoding="utf-8")
        s = cs.load_state()
        # 用户字段保留
        self.assertTrue(s.downloaded)
        self.assertEqual(s.pid, 12345)
        # 缺省字段兜底
        self.assertEqual(s.port, 18891)
        self.assertEqual(s.state, "not_downloaded")
        self.assertFalse(s.dismissed)

    # ----- 完整 round-trip -----
    def test_save_load_roundtrip(self):
        s = cs.ColibriState(
            downloaded=True, model_path="/tmp/olmoe",
            downloaded_at=1000, download_size=7 * 1024 ** 3,
            port=18892, pid=9999, version="v1.12.1",
            last_health_at=2000, restart_attempts=1,
            state="ready", dismissed=True, dismissed_at=3000,
            last_error="some error")
        self.assertTrue(cs.save_state(s))
        loaded = cs.load_state()
        self.assertEqual(loaded.downloaded, s.downloaded)
        self.assertEqual(loaded.model_path, s.model_path)
        self.assertEqual(loaded.port, s.port)
        self.assertEqual(loaded.pid, s.pid)
        self.assertEqual(loaded.state, s.state)
        self.assertEqual(loaded.dismissed, s.dismissed)
        self.assertEqual(loaded.last_error, s.last_error)

    # ----- save_state 权限错误 → False 不抛 -----
    def test_save_failure_returns_false_no_raise(self):
        # 把 state_path 替换成一个目录(写入会失败)
        p = cs.state_path()
        if p.is_file():
            p.unlink()
        p.mkdir(exist_ok=True)
        s = cs.ColibriState(downloaded=True)
        # 不应抛
        result = cs.save_state(s)
        self.assertFalse(result)
        # 清理
        p.rmdir()

    # ----- update_state 原子 -----
    def test_update_state_atomic(self):
        s1 = cs.load_state()
        self.assertFalse(s1.downloaded)
        cs.update_state(downloaded=True, state="ready", pid=12345)
        s2 = cs.load_state()
        self.assertTrue(s2.downloaded)
        self.assertEqual(s2.state, "ready")
        self.assertEqual(s2.pid, 12345)

    def test_update_state_ignores_unknown_fields(self):
        cs.update_state(downloaded=True, foo="bar_unused")
        s = cs.load_state()
        # foo 不在 dataclass 里,被忽略
        self.assertFalse(hasattr(s, "foo"))
        # downloaded 仍然写入
        self.assertTrue(s.downloaded)

    # ----- is_model_path_set -----
    def test_is_model_path_set_empty_dir(self):
        s = cs.load_state()
        # 默认 model_path 指向空目录
        Path(s.model_path).mkdir(parents=True, exist_ok=True)
        self.assertFalse(cs.is_model_path_set(s))

    def test_is_model_path_set_with_file(self):
        s = cs.load_state()
        Path(s.model_path).mkdir(parents=True, exist_ok=True)
        (Path(s.model_path) / "model.safetensors").touch()
        self.assertTrue(cs.is_model_path_set(s))

    def test_is_model_path_set_no_dir(self):
        s = cs.load_state()
        s.model_path = "/nonexistent/path/to/model"
        self.assertFalse(cs.is_model_path_set(s))

    # ----- state_path 在 DATA_DIR 下 -----
    def test_state_path_under_data_dir(self):
        p = cs.state_path()
        self.assertTrue(str(p).startswith(self._tmpdir))
        self.assertTrue(str(p).endswith("colibri.json"))

    # ----- DEFAULT_PORT 不漂 -----
    def test_default_port_pinned(self):
        # 钉死 18891 防漂(M3.35 经验)
        self.assertEqual(cs.DEFAULT_PORT, 18891)


if __name__ == "__main__":
    unittest.main(verbosity=2)