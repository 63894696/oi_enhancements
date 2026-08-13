"""本地配置加载 + token 管理。

token 由整合包安装时随机生成,写两处:
  - 扩展 chrome.storage.local(agentToken)
  - coworker 本地配置文件(本模块读的这份)

token 绝不进 LLM、不写日志。文件权限尽量收紧(仅当前用户可读)。
"""
from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from pathlib import Path

# 默认端口(安装时可改)。coworker §3:如 12450。
DEFAULT_PORT = 12450
HOST = "127.0.0.1"  # 红线①:只监听回环,任何非 127.0.0.1 绑定都拒绝。

# 配置文件默认路径:~/.oiagent/coworker.json(可用 OI_COWORKER_CONFIG 覆盖,便于测试)。
def config_path() -> Path:
    env = os.environ.get("OI_COWORKER_CONFIG")
    if env:
        return Path(env)
    return Path.home() / ".oiagent" / "coworker.json"


def _restrict_perms(p: Path) -> None:
    """尽量把配置文件权限收紧为仅当前用户可读(Windows 上 chmod 作用有限,尽力而为)。"""
    try:
        os.chmod(p, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass  # Windows 不保证生效;不阻断启动。


def load_or_create_token() -> str:
    """读配置里的 token;没有则生成一个随机 token 写回(安装时配对的兜底)。

    正式部署应由整合包安装脚本生成 token 并写两边;这里生成只是「首启兜底」,
    保证开箱即可用且 token 不为空/不硬编码。
    """
    p = config_path()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            tok = str(data.get("token", "")).strip()
            if tok:
                return tok
        except (json.JSONDecodeError, OSError):
            pass  # 配置损坏则重建
    tok = secrets.token_hex(32)  # 64 hex chars
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"token": tok, "port": DEFAULT_PORT}, indent=2), encoding="utf-8")
    _restrict_perms(p)
    return tok


def get_port() -> int:
    env = os.environ.get("OI_COWORKER_PORT")
    if env and env.isdigit():
        return int(env)
    p = config_path()
    if p.exists():
        try:
            return int(json.loads(p.read_text(encoding="utf-8")).get("port", DEFAULT_PORT))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    return DEFAULT_PORT


def ensure_loopback(host: str) -> None:
    """红线①硬校验:只允许 127.0.0.1 / localhost。否则拒起。"""
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise SystemExit(f"红线:openwork 只能监听 127.0.0.1,拒绝绑定 {host!r}")
