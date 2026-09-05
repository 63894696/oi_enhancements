"""simplex_a2h.py — Agent-First OS L2/L3 功能块②:A2H 审批通道(Agent-to-Human)

设计依据:Documents/prisiragent-os-integration/capability-02-a2h-approval.md。
agent 执行敏感操作前,经 SimpleX 加密通道向受信任审批人推确认卡片,人端回复
'yes/no <request_id>' 裁决,fail-closed(超时/异常=拒绝)。零官方依赖,纯应用层
协议,复用 simplex_send_message(发卡片)+ 收件箱事件流(读裁决)。

安全红线:
  - 只认 approver 的 contact_id;其他联系人的 yes/no 一律忽略(防冒充)。
  - 回复必须 'yes/no <request_id>' 严格格式 + 精确 id 匹配(防误判闲聊)。
  - fail-closed:超时/审批人失效/发送失败一律视为拒绝,绝不因等不到人就放行。
"""

from __future__ import annotations

import secrets
import sys
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simplex_runtime import SimplexRuntime  # noqa: E402


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


def simplex_a2h_set_approver(contact: str) -> dict[str, Any]:
    """绑定/更换受信任审批人(那个 SimpleX 联系人,通常是用户自己的手机)。"""
    err = _require_running()
    if err:
        return err
    rt = _runtime()
    try:
        resolved = rt.resolve_contact(contact)
    except Exception as e:  # noqa: BLE001
        return _err("查询联系人失败", f"client 异常:{e!r}")
    if resolved is None:
        return _err(
            f"没有联系人 '{contact}'",
            "审批人必须先是一个已建立的 SimpleX 联系人(建议=用户自己的手机端)。"
            "先 simplex_create_invitation 生成邀请让你的手机接受,或 simplex_accept_invitation 接受手机的名片。",
        )
    if not resolved.get("active"):
        return _err(
            f"联系人 '{contact}' 连接未激活",
            "审批人连接未激活。用 simplex_server_status 查服务器,或重新建立联系。",
        )
    rt.set_a2h_approver(resolved["contact_id"], resolved["display_name"])
    return _ok(
        {"approver": resolved["display_name"], "contact_id": resolved["contact_id"]},
        diagnosable=f"审批人已设为 {resolved['display_name']}(仅认此联系人的 yes/no 裁决)。",
    )


def simplex_a2h_request(action: str, reason: str, timeout: float = 300.0) -> dict[str, Any]:
    """就一项敏感操作向审批人发起加密确认,阻塞等裁决。fail-closed。"""
    err = _require_running()
    if err:
        return err
    rt = _runtime()
    if rt._a2h_approver_cid is None:
        return _err(
            "未设置审批人",
            "先 simplex_a2h_set_approver 绑定你的手机端联系人。审批是敏感操作的必要前提。",
        )
    request_id = "a2h-" + secrets.token_hex(2)  # 4 hex,短小易手输
    try:
        result = rt.a2h_request(request_id, action, reason, timeout)
    except Exception as e:  # noqa: BLE001
        return _err("审批请求异常", f"fail-closed 按拒绝。原始错误:{e!r}", approved=False)

    if result.get("error") and not result.get("timeout"):
        return _err("审批未能完成", f"fail-closed 按拒绝。{result['error']}", approved=False)
    if result.get("timeout"):
        return _err(
            "审批超时未裁决",
            f"{timeout}s 内审批人未回复,fail-closed 按拒绝。确认手机在线/重新发起/调大 timeout。",
            approved=False,
            request_id=request_id,
        )
    approved = result.get("approved", False)
    return _ok(
        result,
        diagnosable=(
            f"审批人已批准,操作放行(耗时 {result.get('latency_s')}s)。"
            if approved else "审批人拒绝,操作已中止。"
        ),
        approved=approved,
    )


def simplex_a2h_pending() -> dict[str, Any]:
    """列出未裁决的审批请求(防丢单)。"""
    err = _require_running()
    if err:
        return err
    items = _runtime().a2h_pending()
    return _ok(
        items,
        count=len(items),
        diagnosable=(f"{len(items)} 个待裁决审批。" if items else "无待裁决审批。"),
    )


# ────────────────────────────────────────────────────────────────────── #
# PolicyEngine 接线:ask 分支的 A2H 裁决回调
# ────────────────────────────────────────────────────────────────────── #

def a2h_approval_callback(tool: str, args: dict[str, Any], timeout: float = 300.0) -> bool:
    """供 PolicyEngine 的 ask 分支调用:发起 A2H 审批,返回 True=批准放行 / False=拒绝。

    daemon 集成时,policy ask 不再"记录后放行",而是 `return a2h_approval_callback(tool, args)`。
    未设审批人或任何异常都 fail-closed 返回 False。
    """
    action = f"{tool}({', '.join(f'{k}={str(v)[:40]}' for k, v in list(args.items())[:4])})"
    reason = "敏感操作待本人确认(A2H)"
    res = simplex_a2h_request(action, reason, timeout)
    return bool(res.get("approved"))


# ────────────────────────────────────────────────────────────────────── #
# 工具注册(schema + 分发)
# ────────────────────────────────────────────────────────────────────── #

_TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "simplex_a2h_set_approver": simplex_a2h_set_approver,
    "simplex_a2h_request": simplex_a2h_request,
    "simplex_a2h_pending": simplex_a2h_pending,
}


def get_tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "simplex_a2h_set_approver",
                "description": "绑定/更换 A2H 受信任审批人(建议=用户自己的手机端联系人)。审批只认此联系人的 yes/no 裁决。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "审批人联系人 display name 或 contact_id"},
                    },
                    "required": ["contact"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_a2h_request",
                "description": "就一项敏感操作向审批人发起 E2E 加密确认,阻塞等裁决(超时按拒绝,fail-closed)。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "description": "待审批动作描述(工具名+关键参数)"},
                        "reason": {"type": "string", "description": "为何需要人审"},
                        "timeout": {"type": "number", "description": "等待秒数,默认 300", "default": 300},
                    },
                    "required": ["action", "reason"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_a2h_pending",
                "description": "列出未裁决的 A2H 审批请求(防丢单)。",
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
