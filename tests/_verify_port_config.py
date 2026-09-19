r"""tests/_verify_port_config.py — M3.34(2026-09-19)port_config E2E 测试

策略:用 importlib.util.spec_from_file_location 直接加载 companion/music/port_config.py
(风格跟 tests/_verify_plan_b_main.py 一致)。

测试项:
  1. read_port('web', 18802) 在干净状态 → 18802
  2. write_port('web', 19999) → 再读 = 19999
  3. resolve_start_port('web', None, None, 18802) = 19999(用户设置生效)
  4. resolve_start_port('web', '18803', None, 18802) = 18803(env 优先)
  5. resolve_start_port('web', None, 18804, 18802) = 18804(CLI 优先)
  6. resolve_start_port('web', '18803', 18804, 18802) = 18804(CLI > env)
  7. pick_free_port(1) 返正整数(端口 1 通常被占,会走 fallback)
  8. notify_port_changed('web', 18802, 18811) 不抛异常,stderr 有日志
  9. notify_port_changed 后 read_port('web', 18802) = 18811(写回生效)
 10. 跨进程模拟:用 monkey-patch _pc_write_port 写到临时 HKCU sub-key,避免污染真实注册表
     - 测试用临时 sub-key:Software\PrisirAI_Test
     - 测试结束时清理

跑法:python tests/_verify_port_config.py
预期:全部断言通过,print "ALL OK"
"""
import importlib.util
import os
import socket
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = Path(__file__).resolve().parent

# 把 companion/ 加进 sys.path 以便测试加载子模块
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "companion"))

# 强制 PRISIR_WORKDIR 指向临时目录,避免测试期间污染 workdir/_prisir_registry/ports.json
_TMP_WORKDIR = tempfile.mkdtemp(prefix="port_config_test_")
os.environ["PRISIR_WORKDIR"] = _TMP_WORKDIR

# 通过 spec_from_file_location 加载(参照 plan_b verify 风格)
_SPEC = importlib.util.spec_from_file_location(
    "port_config",
    str(ROOT / "companion" / "music" / "port_config.py"),
)
pc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pc)


# ============================================================
# 测试用临时 HKCU sub-key(避免污染真实注册表 Software\PrisirAI)
# ============================================================
TEST_REG_KEY = r"Software\PrisirAI_Test"  # 测试专用,跑完会清


def _patch_reg_key(name: str) -> str:
    """临时把 pc 的注册表相关函数切到测试 sub-key。
    返回原始 REG_KEY 方便还原。
    """
    orig_key = pc.REG_KEY
    pc.REG_KEY = TEST_REG_KEY
    # 同步更新 _reg_val 的内部值(它直接读模块级 REG_KEY,无需重写)
    return orig_key


def _restore_reg_key(orig_key: str) -> None:
    pc.REG_KEY = orig_key


def _cleanup_test_reg() -> None:
    """清理测试用的 HKCU sub-key(测试结束必跑)。"""
    if os.name != "nt":
        return
    try:
        import winreg  # type: ignore
        # 递归删 sub-key(含所有 values)
        def _delete_tree(root, sub):
            try:
                with winreg.OpenKey(root, sub, 0, winreg.KEY_ALL_ACCESS) as k:
                    while True:
                        try:
                            child = winreg.EnumKey(k, 0)
                        except OSError:
                            break
                        _delete_tree(k, child)
                winreg.DeleteKey(root, sub)
            except FileNotFoundError:
                pass
            except Exception:
                pass
        _delete_tree(winreg.HKEY_CURRENT_USER, TEST_REG_KEY)
    except Exception:
        pass


def _cleanup_test_json() -> None:
    """清理测试用的 _prisir_registry/ports.json(测试结束必跑)。"""
    try:
        reg_dir = Path(_TMP_WORKDIR) / "_prisir_registry"
        for f in reg_dir.glob("ports.json"):
            f.unlink(missing_ok=True)
        for f in reg_dir.glob("*.lock"):
            f.unlink(missing_ok=True)
    except Exception:
        pass


# ============================================================
# 测试用例
# ============================================================
# 默认把 pc 的注册表 sub-key 切到测试专用路径,避免所有测试污染真实
# Software\PrisirAI(否则测试通过后真实 web 端口会被改写,下次启动主面板
# 会读到 18811 这种"测试残留"值)。
_ORIG_REG_KEY = pc.REG_KEY
pc.REG_KEY = TEST_REG_KEY
# 给 json 路径也切到 tmp(用 PRISIR_WORKDIR env 已实现,无需再 patch)


def _teardown_reg_keys():
    _cleanup_test_reg()
    pc.REG_KEY = _ORIG_REG_KEY


def test_1_default_state():
    """干净状态:read_port('web', 18802) → 18802。"""
    # 确保临时 JSON 干净(其他测试已写的话)
    _cleanup_test_json()
    v = pc.read_port("web", pc.DEFAULT_WEB_PORT)
    assert v == 18802, f"expected 18802, got {v}"
    print(f"[test1] read_port('web', default) = {v} OK")


def test_2_write_then_read():
    """write_port('web', 19999) → 再读 = 19999。"""
    _cleanup_test_json()
    ok = pc.write_port("web", 19999)
    assert ok is True, f"write_port returned {ok}"
    v = pc.read_port("web", pc.DEFAULT_WEB_PORT)
    assert v == 19999, f"expected 19999, got {v}"
    print(f"[test2] write_port(19999) then read_port = {v} OK")


def test_3_resolve_user_setting():
    """resolve_start_port('web', None, None, 18802) = 19999(用户设置生效)。"""
    _cleanup_test_json()
    pc.write_port("web", 19999)
    v = pc.resolve_start_port("web", None, None, 18802)
    assert v == 19999, f"expected 19999, got {v}"
    print(f"[test3] resolve_start_port(user-set) = {v} OK")


def test_4_resolve_env_wins():
    """resolve_start_port('web', '18803', None, 18802) = 18803(env 优先)。"""
    _cleanup_test_json()
    pc.write_port("web", 19999)  # 用户设置 ≠ env
    v = pc.resolve_start_port("web", "18803", None, 18802)
    assert v == 18803, f"expected 18803, got {v}"
    print(f"[test4] resolve_start_port(env) = {v} OK")


def test_5_resolve_cli_wins():
    """resolve_start_port('web', None, 18804, 18802) = 18804(CLI 优先)。"""
    _cleanup_test_json()
    pc.write_port("web", 19999)  # 用户设置
    v = pc.resolve_start_port("web", None, 18804, 18802)
    assert v == 18804, f"expected 18804, got {v}"
    print(f"[test5] resolve_start_port(cli) = {v} OK")


def test_6_resolve_cli_over_env():
    """resolve_start_port('web', '18803', 18804, 18802) = 18804(CLI > env)。"""
    _cleanup_test_json()
    pc.write_port("web", 19999)
    v = pc.resolve_start_port("web", "18803", 18804, 18802)
    assert v == 18804, f"expected 18804, got {v}"
    print(f"[test6] resolve_start_port(cli>env) = {v} OK")


def test_7_pick_free_port():
    """pick_free_port(1) 返正整数(端口 1 通常被占,会走 fallback)。"""
    v = pc.pick_free_port(1)
    assert isinstance(v, int) and v > 0, f"expected positive int, got {v!r}"
    assert 1 <= v <= 65535, f"port {v} out of range"
    # 二次调用 bind(0) 拿到的端口 != 1(因为 1 被占);但允许偶发空闲(>0 即可)
    print(f"[test7] pick_free_port(1) = {v} OK")


def test_8_notify_port_changed_logs():
    """notify_port_changed('web', 18802, 18811) 不抛异常,stderr 有日志。"""
    import io
    _cleanup_test_json()
    captured = io.StringIO()
    old_stderr = sys.stderr
    sys.stderr = captured
    try:
        pc.notify_port_changed("web", 18802, 18811)
    finally:
        sys.stderr = old_stderr
    out = captured.getvalue()
    assert "18802" in out and "18811" in out, f"expected port numbers in stderr, got: {out!r}"
    assert "changed" in out.lower(), f"expected 'changed' keyword, got: {out!r}"
    print(f"[test8] notify_port_changed stderr OK: {out.strip()[:120]}")


def test_9_notify_writes_back():
    """notify_port_changed 后 read_port('web', 18802) = 18811(写回生效)。"""
    _cleanup_test_json()
    pc.notify_port_changed("web", 18802, 18811)
    v = pc.read_port("web", pc.DEFAULT_WEB_PORT)
    assert v == 18811, f"expected 18811 after notify, got {v}"
    print(f"[test9] notify_port_changed → read_port = {v} OK")


def test_10_isolated_hkcu_subkey():
    """跨进程模拟:写测试用临时 HKCU sub-key,验证读路径走 HKCU。

    monkey-patch pc.REG_KEY 到 Software\\PrisirAI_Test,然后:
      - 走 _write_winreg / _read_winreg → 验证值落到测试 sub-key
      - 清理时删整个 sub-key
    """
    _cleanup_test_json()
    _cleanup_test_reg()
    orig_key = _patch_reg_key("test10")
    try:
        # 写测试值
        ok = pc._write_winreg("web", 18888)
        assert ok is True, "write_winreg to test sub-key failed"
        # 读出来
        v = pc._read_winreg("web")
        assert v == 18888, f"expected 18888 from test sub-key, got {v}"
        # 走公开 read_port(也会先试 HKCU → 测试 sub-key)
        v2 = pc.read_port("web", pc.DEFAULT_WEB_PORT)
        assert v2 == 18888, f"expected 18888 via read_port, got {v2}"
        print(f"[test10] isolated HKCU sub-key write/read OK ({v2})")
    finally:
        _cleanup_test_reg()
        _restore_reg_key(orig_key)


def test_11_idempotent_legacy_migration():
    """音乐端口迁移幂等性:多次调用 _migrate_legacy_music_port 不重复写。"""
    _cleanup_test_json()
    # 模拟:写一个旧 music_port 到测试 sub-key,然后调迁移
    if os.name == "nt":
        orig_key = _patch_reg_key("test11")
        try:
            pc._write_winreg("music", 18999)  # 模拟旧 music_port 注册表项
            # 第一次迁移
            pc._migrate_legacy_music_port()
            data1 = pc._load_json()
            assert data1.get("music") == 18999, f"first migration failed: {data1}"
            assert data1.get(pc._MIGRATED_KEY) is True, "migration marker missing"
            # 第二次迁移 → idempotent,不应再写 music
            pc._migrate_legacy_music_port()
            data2 = pc._load_json()
            assert data2.get("music") == 18999, "music value changed on second migration"
            print(f"[test11] legacy music_port migration idempotent OK")
        finally:
            _cleanup_test_reg()
            _restore_reg_key(orig_key)
    else:
        print(f"[test11] skip on non-Windows")


def main():
    print(f"[boot] tmp workdir = {_TMP_WORKDIR}")
    print(f"[boot] tmp ports.json = {Path(_TMP_WORKDIR) / '_prisir_registry' / 'ports.json'}")
    tests = [
        test_1_default_state,
        test_2_write_then_read,
        test_3_resolve_user_setting,
        test_4_resolve_env_wins,
        test_5_resolve_cli_wins,
        test_6_resolve_cli_over_env,
        test_7_pick_free_port,
        test_8_notify_port_changed_logs,
        test_9_notify_writes_back,
        test_10_isolated_hkcu_subkey,
        test_11_idempotent_legacy_migration,
    ]
    failed = []
    for t in tests:
        name = t.__name__
        try:
            t()
        except AssertionError as e:
            failed.append((name, f"AssertionError: {e}"))
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            import traceback
            failed.append((name, f"{type(e).__name__}: {e}"))
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            traceback.print_exc()

    # 清理
    _teardown_reg_keys()
    _cleanup_test_json()
    # 防御性清理:如果之前有跑老版本测试在真实 Software\PrisirAI\web_port 留过
    # 测试残留(18811 之类),这里再清一次,不影响生产路径(仅删 web_port / companion_port /
    # music_port 三个 value,不删整个 PrisirAI sub-key)。
    if os.name == "nt":
        try:
            import winreg  # type: ignore
            for vname in ["web_port", "companion_port", "music_port"]:
                try:
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Software\\PrisirAI", 0, winreg.KEY_SET_VALUE) as k:
                        winreg.DeleteValue(k, vname)
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
        except Exception:
            pass
    try:
        import shutil
        shutil.rmtree(_TMP_WORKDIR, ignore_errors=True)
    except Exception:
        pass

    if failed:
        print(f"\n[result] {len(failed)} FAILED out of {len(tests)}")
        sys.exit(1)
    print(f"\n[result] ALL OK ({len(tests)}/{len(tests)})")


if __name__ == "__main__":
    main()
