"""simplex_rotation.py — Agent-First OS L2 功能块③:会话轮换 / 短生命周期(抗纵向去匿名化)

设计依据:Documents/prisiragent-os-integration/capability-03-session-rotation.md;社区 #6940。
长命 agent 通道会把数月消息链接到同一标识(职业/城市/机构拼起来就识别了人)。本块让
agent 定期"换脸"——同连接内换假名(rename),切断纵向可链接性,用户零干预(agent 自动化 OPSEC)。

策略:
  - rename(默认,无感):改当前身份显示名。消息流不中断,对端看到新名。
  - reconnect(敏感,走 A2H):生成新一次性邀请重建连接。本阶段仅在 A2A/对端可自动化时
    由 agent 编排(对端是人需其手动点链接,故默认走 A2H 审批确认)。

诚实边界(对齐 #6940):本块不能防 relay/admin、时序分析、写作风格指纹、内容自我披露、
设备被控;它移除的是"普通参与者在协议层自动链接消息"的能力。
"""

from __future__ import annotations

import secrets
import sys
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simplex_runtime import SimplexRuntime  # noqa: E402

try:
    import simplex_a2h as _a2h  # reconnect 走 A2H 审批
except Exception:  # noqa: BLE001
    _a2h = None


def _ok(output: Any, **extra) -> dict[str, Any]:
    return {"ok": True, "output": output, **extra}


def _err(reason: str, diagnosable: str, **extra) -> dict[str, Any]:
    return {"ok": False, "error": reason, "diagnosable": diagnosable, **extra}


def _runtime() -> SimplexRuntime:
    return SimplexRuntime.instance()


def _require_running() -> dict[str, Any] | None:
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup 初始化身份并连接服务器。")
    return None


def _new_alias(base: str = "prisiragent") -> str:
    """生成随机后缀的新假名,避免与旧名语义关联。"""
    return f"{base}-{secrets.token_hex(2)}"


def simplex_rotate_identity(strategy: str = "rename", contact: str | None = None,
                            new_name: str | None = None, require_approval: bool = True) -> dict[str, Any]:
    """为长命通道执行一次身份轮换,切断纵向可链接性。

    strategy:
      - rename   : 仅换显示名(默认,无感,消息流不中断)
      - reconnect: 换假名 + 生成新邀请重建连接(敏感,默认先 A2H 审批)
    """
    err = _require_running()
    if err:
        return err
    rt = _runtime()
    strategy = (strategy or "rename").lower()
    if strategy not in ("rename", "reconnect", "full"):
        return _err(f"未知策略 '{strategy}'", "strategy ∈ {rename, reconnect, full}。rename=仅换名;reconnect/full=换名+重建连接。")

    # reconnect/full 是敏感操作:先 A2H 审批(fail-closed)
    if strategy in ("reconnect", "full") and require_approval:
        if _a2h is None or rt._a2h_approver_cid is None:
            return _err(
                "reconnect 需要 A2H 审批但未配置",
                "重建连接是敏感操作。先 simplex_a2h_set_approver 绑定审批人,或显式 require_approval=False(仅 A2A 场景)。",
            )
        res = _a2h.simplex_a2h_request(f"rotate_identity(strategy={strategy})", "身份轮换重建连接", 300)
        if not res.get("approved"):
            return _err(
                "轮换被审批拒绝/超时",
                "fail-closed:本次不轮换,保留旧连接与消息。审批人确认后重试。",
                approved=False,
            )

    # rename(所有策略都先换名)
    alias = new_name or _new_alias()
    try:
        ren = rt.update_display_name(alias)
    except Exception as e:  # noqa: BLE001
        return _err("换假名失败", f"update_display_name 异常:{e!r}")

    out: dict[str, Any] = {"strategy": strategy, "old_alias": ren.get("old_name"), "new_alias": ren.get("new_name")}
    if strategy == "rename":
        return _ok(
            out,
            diagnosable=f"显示名已从 '{ren.get('old_name')}' 轮换为 '{ren.get('new_name')}',消息流不中断,对端看到新名。",
        )

    # reconnect/full:生成新一次性邀请,供对端重建连接
    try:
        new_link = rt.create_invitation()
    except Exception as e:  # noqa: BLE001
        return _err("生成新邀请失败", f"换名成功但重建邀请失败:{e!r}。旧连接仍保留(fail-closed)。", **out)
    out["new_invite"] = new_link
    return _ok(
        out,
        diagnosable=(
            f"已换名为 '{ren.get('new_name')}' 并生成新一次性邀请。"
            "对端用 simplex_accept_invitation 接受即建立新连接;旧连接在对端接入后弃用。"
            "对端是 agent 时可自动接受,是人时需其点链接。"
        ),
    )


def simplex_current_identity() -> dict[str, Any]:
    """查看当前身份(显示名/服务器/审批人),供轮换前确认。"""
    err = _require_running()
    if err:
        return err
    rt = _runtime()
    try:
        st = rt.status()
    except Exception as e:  # noqa: BLE001
        return _err("状态查询失败", f"{e!r}")
    st["a2h_approver"] = rt._a2h_approver_name
    return _ok(st, diagnosable=f"当前身份:{st.get('active_user')}。")


# ────────────────────────────────────────────────────────────────────── #
# 工具注册(schema + 分发)
# ────────────────────────────────────────────────────────────────────── #

_TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "simplex_rotate_identity": simplex_rotate_identity,
    "simplex_current_identity": simplex_current_identity,
}


def get_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "simplex_rotate_identity",
                "description": "为长命通道执行身份轮换(换假名/重建连接),切断纵向可链接性。rename 无感;reconnect 走 A2H 审批。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "strategy": {"type": "string", "description": "rename(默认,仅换名) | reconnect(换名+重建) | full", "default": "rename"},
                        "new_name": {"type": "string", "description": "可选:指定新假名;缺省随机生成"},
                        "require_approval": {"type": "boolean", "description": "reconnect 是否需 A2H 审批,默认 true", "default": True},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_current_identity",
                "description": "查看当前 SimpleX 身份(显示名/服务器/审批人),供轮换前确认。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
    ]


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        return _err(f"未知工具 '{name}'", f"可用工具:{sorted(_TOOL_IMPLS)}")
    try:
        return impl(**args)
    except TypeError as e:
        return _err(f"参数错误:{e}", f"工具 {name} 的 schema 见 get_tools();检查参数名/类型。")
    except Exception as e:  # noqa: BLE001
        return _err(f"工具 {name} 执行异常", f"{e!r}")


TOOL_NAMES = sorted(_TOOL_IMPLS)
