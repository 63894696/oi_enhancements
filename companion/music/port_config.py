# -*- coding: utf-8 -*-
r"""
port_config.py — PrisirAI 全栈端口配置(2026-09-19)

目的
====
把三个进程的端口(主面板 / 语伴 / 音乐 web)统一进一个存储。
旧实现分散在:
  - prisIragent_web.py    : 写死 18802(env PRISIRAGENT_WEB_PORT 覆盖)
  - prisIragent-companion-web.py : 写死 18850(仅 --port CLI)
  - music/port_registry.py : HKCU\Software\PrisirAI\music_port(music-only)

新模块做:
  - HKCU\Software\PrisirAI\<name>_port (DWORD) 主通道
  - <workdir>/_prisir_registry/ports.json 跨平台 fallback(沿用 port_registry 目录)
  - 模块默认(代码内置)

与 port_registry.py 的关系
==========================
  - port_registry.py 是旧的 music-only 实现,本模块是新统一版。
  - 旧 music_port 注册表项在 read_port('music') 时一次性迁移:
      读到 music_port(DWORD) → 写到 ports.json['music'] → 删除旧 music_port 注册表项
    迁移是 idempotent 的:迁移完后再次调用 read_port 不会再触发迁移。
  - 本模块不删 port_registry.py(其它模块可能还在用 read_music_port / write_music_port),
    但新代码应改用 port_config 的 read_port / write_port。

启动优先级(供 resolve_start_port 使用)
====================================
  CLI 参数 > 环境变量 > 用户设置(HKCU/JSON) > 模块默认
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Optional


# 模块默认(代码内置)
DEFAULT_WEB_PORT = 18802       # 主面板 prisIragent_web.py
DEFAULT_COMPANION_PORT = 18850 # 语伴 companion/prisiragent-companion-web.py
DEFAULT_MUSIC_PORT = 0         # 音乐 web 启动后才有端口(0 = 由 OS 分配)

# Windows 注册表路径(主通道)
REG_KEY = r"Software\PrisirAI"
REG_VAL_SUFFIX = "_port"  # <name>_port,例如 web_port / companion_port / music_port

# 旧 music 端口注册表项(M3.29.1 引入,2026-09-19 弃用)
LEGACY_MUSIC_REG_VAL = "music_port"

# 跨平台 fallback(JSON 文件,与 port_registry.py 共用目录)
_REG_DIR = Path(os.environ.get("PRISIR_WORKDIR", str(Path.cwd()))) / "_prisir_registry"
_REG_DIR.mkdir(parents=True, exist_ok=True)
_JSON_PATH = _REG_DIR / "ports.json"


# ------------------------------------------------------------
# JSON fallback 读写
# ------------------------------------------------------------
def _load_json() -> dict:
    """读 ports.json;不存在/解析失败 → 空 dict。"""
    try:
        if not _JSON_PATH.exists():
            return {}
        data = json.loads(_JSON_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_json(data: dict) -> None:
    """写 ports.json;失败静默(主通道是注册表,JSON 只是 fallback)。"""
    try:
        _JSON_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def _reg_val(name: str) -> str:
    """注册表项名。name ∈ {web, companion, music}"""
    safe = str(name).strip().lower()
    return f"{safe}{REG_VAL_SUFFIX}"


# ------------------------------------------------------------
# Windows 注册表通道
# ------------------------------------------------------------
def _write_winreg(name: str, port: int) -> bool:
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, _reg_val(name), 0, winreg.REG_DWORD, int(port))
        return True
    except Exception:
        return False


def _read_winreg(name: str) -> Optional[int]:
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
            v, _ = winreg.QueryValueEx(k, _reg_val(name))
            return int(v)
    except FileNotFoundError:
        return None
    except Exception:
        return None


def _delete_winreg(name: str) -> bool:
    """删除注册表项(供旧 music_port 迁移用)。"""
    if os.name != "nt":
        return False
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, _reg_val(name))
        return True
    except FileNotFoundError:
        return True  # 已经不存在 = 目标达成
    except Exception:
        return False


# ------------------------------------------------------------
# 旧 music_port 迁移(idempotent)
# ------------------------------------------------------------
_MIGRATED_KEY = "__music_legacy_migrated__"


def _migrate_legacy_music_port() -> None:
    """把 HKCU\\Software\\PrisirAI\\music_port 迁移到 ports.json['music']。

    仅迁移一次(通过 ports.json 标记位保证幂等)。
    """
    data = _load_json()
    if data.get(_MIGRATED_KEY):
        return
    legacy = _read_winreg("music")  # 读 HKCU ... PrisirAI\music_port
    if legacy is not None:
        # 写到 JSON(若 JSON 还没有 music 字段,避免覆盖新值)
        if "music" not in data:
            data["music"] = int(legacy)
        data[_MIGRATED_KEY] = True
        _save_json(data)
        # 注册表旧项可删(迁移已落地,旧值不再需要)
        try:
            _delete_winreg("music")
        except Exception:
            pass
    else:
        # 即便没找到旧注册表项,也标记为已迁移,避免每次 read 都扫注册表
        if _MIGRATED_KEY not in data:
            data[_MIGRATED_KEY] = True
            _save_json(data)


# ------------------------------------------------------------
# 公开 API
# ------------------------------------------------------------
def read_port(name: str, default: int) -> int:
    """读用户配置的端口(name ∈ {web, companion, music} 等)。

    优先级:HKCU 注册表 → JSON fallback → 模块默认。
    端口值必须合法(1-65535)否则按 fallback 降级。

    对 'music' 触发一次性旧注册表项迁移。
    """
    if name == "music":
        try:
            _migrate_legacy_music_port()
        except Exception:
            pass  # 迁移失败不影响读取

    v = _read_winreg(name)
    if v is not None and 1 <= v <= 65535:
        return int(v)

    data = _load_json()
    jv = data.get(name)
    if isinstance(jv, int) and 1 <= jv <= 65535:
        return int(jv)

    return int(default)


def write_port(name: str, port: int) -> bool:
    """写用户配置的端口(HKCU + JSON 双写)。

    返回 True = 至少一个通道写成功;False = 两个通道都失败。
    """
    p = int(port)
    win_ok = _write_winreg(name, p)
    try:
        data = _load_json()
        data[name] = p
        data[f"{name}_updated_at_ms"] = int(time.time() * 1000)
        _save_json(data)
        json_ok = True
    except Exception:
        json_ok = False
    return bool(win_ok or json_ok)


def pick_free_port(prefer: int) -> int:
    """优先用 prefer;若占用则 bind(0) 拿空闲端口。

    prefer 不合法(<=0 或 >65535)→ 直接 bind(0)。
    prefer 合法 → 先试 bind(prefer);占用 OSError → fallback bind(0)。
    """
    # 合法 prefer 才试
    if isinstance(prefer, int) and 1 <= prefer <= 65535:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", prefer))
                return prefer  # prefer 没被占,直接用
            except OSError:
                pass  # 占用,走 fallback
        finally:
            try:
                s.close()
            except Exception:
                pass

    # fallback:bind(0) 拿空闲端口
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        try:
            s.close()
        except Exception:
            pass


def _coerce_int(value, current_default: int) -> Optional[int]:
    """把 CLI/env 的字符串/None 转 int;失败返 None。"""
    if value is None:
        return None
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(s)
    except (ValueError, TypeError):
        return None


def resolve_start_port(name: str, env_value, cli_value, default: int) -> int:
    """合并启动端口:CLI > env > 用户设置(HKCU/JSON) > default。

    name        : 'web' / 'companion' / 'music' 等
    env_value   : os.environ.get('PRISIRAGENT_<NAME>_PORT') 等,字符串/None
    cli_value   : argparse 解析后的 int 或 None
    default     : 模块默认(DEFAULT_WEB_PORT / DEFAULT_COMPANION_PORT 等)
    """
    cli = _coerce_int(cli_value, default)
    if cli is not None and 1 <= cli <= 65535:
        return cli

    env = _coerce_int(env_value, default)
    if env is not None and 1 <= env <= 65535:
        return env

    # 用户设置 → 用 default 占位(因为 read_port 必返一个 int)
    return read_port(name, int(default))


def notify_port_changed(name: str, old: int, new: int) -> None:
    """实际端口与配置端口不同时:写回注册表 + stderr 日志。

    用途:UI 端通过 /api/port_status 拿到 changed=true 弹一次性 toast。
    日志格式:'port <name> changed: <old> -> <new> (due to conflict)'。
    """
    try:
        if int(new) != int(old):
            write_port(name, int(new))
            sys.stderr.write(
                f"[port_config] port {name} changed: {int(old)} -> {int(new)} (due to conflict)\n"
            )
            sys.stderr.flush()
    except Exception as e:  # noqa: BLE001 — 写日志失败不能让进程崩
        try:
            sys.stderr.write(f"[port_config] notify_port_changed failed: {e}\n")
            sys.stderr.flush()
        except Exception:
            pass


# ------------------------------------------------------------
# 烟囱测试(直接 python port_config.py 跑)
# ------------------------------------------------------------
def quick_smoke() -> dict:
    return {
        "DEFAULT_WEB_PORT": DEFAULT_WEB_PORT,
        "DEFAULT_COMPANION_PORT": DEFAULT_COMPANION_PORT,
        "read_web": read_port("web", DEFAULT_WEB_PORT),
        "read_companion": read_port("companion", DEFAULT_COMPANION_PORT),
        "read_music": read_port("music", DEFAULT_MUSIC_PORT),
        "pick_free": pick_free_port(DEFAULT_WEB_PORT),
        "resolve_default": resolve_start_port("web", None, None, DEFAULT_WEB_PORT),
    }


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
