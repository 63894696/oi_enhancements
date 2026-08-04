"""test_simplex_integrity_dl_dir.py — verify 下载目录候选合并逻辑测试

不连真 SimpleX 服务器:mock _runtime + simplex_verify_received_file,用 tmp 目录
覆盖"默认目录 + 自定义目录"两候选合并取最新的逻辑(_candidate_download_dirs +
simplex_verify_file_by_manifest 的文件定位段)。

跑法:`python -m unittest test_simplex_integrity_dl_dir.py -v`
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import simplex_integrity as si  # noqa: E402


def _fake_rt(download_dir: str, db_prefix: str = ""):
    rt = mock.Mock()
    rt._thread.is_alive.return_value = True
    rt._file_download_dir = download_dir
    rt._db_prefix = db_prefix
    return rt


class TestCandidateDownloadDirs(unittest.TestCase):
    """_candidate_download_dirs:默认目录恒在;自定义目录来自 download_dir.txt。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.default = self.root / "default_dl"
        self.default.mkdir()
        # db_prefix 经 mock rt._db_prefix 注入(真实路径来源),download_dir.txt 落在其 parent。
        # 同时清掉 DM_DB_PREFIX 环境变量,证明不依赖 env。
        self.db_prefix = self.root / "db" / "alice_simplex"
        self._env = mock.patch.dict(os.environ, {"DM_DB_PREFIX": ""})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _rt(self) -> mock.Mock:
        return _fake_rt(str(self.default), db_prefix=str(self.db_prefix))

    def _write_custom(self, path: str) -> None:
        self.db_prefix.parent.mkdir(parents=True, exist_ok=True)
        (self.db_prefix.parent / "download_dir.txt").write_text(path, encoding="utf-8")

    def test_no_custom_returns_only_default(self):
        dirs = si._candidate_download_dirs(self._rt())
        self.assertEqual(dirs, [self.default])

    def test_custom_appended(self):
        custom = self.root / "custom_dl"
        self._write_custom(str(custom))
        dirs = si._candidate_download_dirs(self._rt())
        self.assertEqual(dirs, [self.default, custom])

    def test_custom_same_as_default_deduped(self):
        self._write_custom(str(self.default))
        dirs = si._candidate_download_dirs(self._rt())
        self.assertEqual(dirs, [self.default])

    def test_missing_txt_file_tolerated(self):
        # download_dir.txt 不存在 → 仅默认目录,不抛异常
        self.assertEqual(si._candidate_download_dirs(self._rt()), [self.default])

    def test_unreadable_txt_tolerated(self):
        # txt 路径是个目录(读会抛 IsADirectoryError)→ 静默回退到仅默认目录
        self.db_prefix.parent.mkdir(parents=True, exist_ok=True)
        (self.db_prefix.parent / "download_dir.txt").mkdir()
        self.assertEqual(si._candidate_download_dirs(self._rt()), [self.default])

    def test_empty_db_prefix_no_cwd_shadow(self):
        """db_prefix 全空(env DM_DB_PREFIX="" + rt._db_prefix="")且 CWD 下有 download_dir.txt:
        不得用 Path("").parent=`.` 误读 CWD 的 download_dir.txt(生产 CWD=oi_enhancements,脏数据风险)。"""
        with tempfile.TemporaryDirectory() as cwd:
            Path(cwd, "download_dir.txt").write_text(str(self.root / "evil"), encoding="utf-8")
            old = os.getcwd()
            os.chdir(cwd)
            try:
                rt = _fake_rt(str(self.default), db_prefix="")
                with mock.patch.dict(os.environ, {"DM_DB_PREFIX": ""}):
                    dirs = si._candidate_download_dirs(rt)
            finally:
                os.chdir(old)
        self.assertNotIn(self.root / "evil", dirs)
        self.assertEqual(dirs[0], self.default)


class TestVerifyFileByManifestDirMerge(unittest.TestCase):
    """simplex_verify_file_by_manifest 的两目录合并取最新 + 找不到时的诊断。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.default = self.root / "default_dl"
        self.custom = self.root / "custom_dl"
        self.default.mkdir()
        self.custom.mkdir()
        self.db_prefix = self.root / "db" / "alice_simplex"
        self._env = mock.patch.dict(os.environ, {"DM_DB_PREFIX": ""})
        self._env.start()
        self.addCleanup(self._env.stop)

    def _write_custom_txt(self) -> None:
        self.db_prefix.parent.mkdir(parents=True, exist_ok=True)
        (self.db_prefix.parent / "download_dir.txt").write_text(str(self.custom), encoding="utf-8")

    def _run(self, fname="payload.bin"):
        rt = _fake_rt(str(self.default), db_prefix=str(self.db_prefix))
        with mock.patch.object(si, "_runtime", return_value=rt), \
             mock.patch.object(si, "simplex_verify_received_file",
                               return_value={"ok": True, "output": {"verified": True}}) as vrf:
            r = si.simplex_verify_file_by_manifest("bob", fname, timeout=0.1)
        return r, vrf

    def test_a_only_default_has_file(self):
        (self.default / "payload.bin").write_bytes(b"from-default")
        r, vrf = self._run()
        self.assertTrue(r["ok"])
        self.assertEqual(Path(vrf.call_args[0][1]), self.default / "payload.bin")

    def test_b_only_custom_has_file(self):
        self._write_custom_txt()
        (self.custom / "payload.bin").write_bytes(b"from-custom")
        r, vrf = self._run()
        self.assertTrue(r["ok"])
        self.assertEqual(Path(vrf.call_args[0][1]), self.custom / "payload.bin")

    def test_b2_rt_db_prefix_wins_over_missing_env(self):
        """模拟真实 bob 场景:env 无 DM_DB_PREFIX 也无 identity(bob 是 argv 覆写模块全局、不写 env),
        但 rt._db_prefix 已是 bob 的正确前缀 → 必须用它定位 download_dir.txt,不回退错 identity。
        这是修复前 bug 会复发的关键场景(env 回退 "oiagent" 会找错目录)。"""
        self._write_custom_txt()
        (self.custom / "payload.bin").write_bytes(b"from-custom")
        # 确保 env 完全没有 DM_DB_PREFIX / DM_IDENTITY / SECUREDM_INSTANCE
        clean = {k: v for k, v in os.environ.items()
                 if k not in ("DM_DB_PREFIX", "DM_IDENTITY", "SECUREDM_INSTANCE")}
        rt = _fake_rt(str(self.default), db_prefix=str(self.db_prefix))
        with mock.patch.dict(os.environ, clean, clear=True), \
             mock.patch.object(si, "_runtime", return_value=rt), \
             mock.patch.object(si, "simplex_verify_received_file",
                               return_value={"ok": True, "output": {"verified": True}}) as vrf:
            r = si.simplex_verify_file_by_manifest("bob", "payload.bin", timeout=0.1)
        self.assertTrue(r["ok"])
        self.assertEqual(Path(vrf.call_args[0][1]), self.custom / "payload.bin")

    def test_c_both_dirs_picks_latest_mtime(self):
        self._write_custom_txt()
        f_def = self.default / "payload.bin"
        f_cus = self.custom / "payload.bin"
        f_def.write_bytes(b"old")
        f_cus.write_bytes(b"new")
        # 强制 mtime:默认目录的旧,自定义目录的新
        old = time.time() - 100
        os.utime(f_def, (old, old))
        r, vrf = self._run()
        self.assertTrue(r["ok"])
        self.assertEqual(Path(vrf.call_args[0][1]), f_cus)

    def test_c2_both_dirs_picks_latest_mtime_reversed(self):
        """反向:默认目录新、自定义目录旧 → 取默认目录。"""
        self._write_custom_txt()
        f_def = self.default / "payload.bin"
        f_cus = self.custom / "payload.bin"
        f_def.write_bytes(b"new")
        f_cus.write_bytes(b"old")
        old = time.time() - 100
        os.utime(f_cus, (old, old))
        r, vrf = self._run()
        self.assertTrue(r["ok"])
        self.assertEqual(Path(vrf.call_args[0][1]), f_def)

    def test_d_neither_dir_reports_searched_dirs(self):
        self._write_custom_txt()
        r, vrf = self._run()
        self.assertFalse(r["ok"])
        vrf.assert_not_called()
        # diagnosable 列出实际搜索过的两个目录
        self.assertIn(str(self.default), r["diagnosable"])
        self.assertIn(str(self.custom), r["diagnosable"])

    def test_d2_no_custom_reports_only_default(self):
        r, _ = self._run()
        self.assertFalse(r["ok"])
        self.assertIn(str(self.default), r["diagnosable"])
        self.assertNotIn(str(self.custom), r["diagnosable"])


if __name__ == "__main__":
    unittest.main()
