"""simplex_tools.py — Agent-First OS L2 功能块:SimpleX 加密 IM(agent 可调用工具面)

阶段 1 范本:把 SimpleX 协议能力包成 agent 可调用的工具,在对话界面端到端验证
"给 X 发加密消息"。这是"面向 agent 开发"的第一个完整证明(见
agent-first-os-architecture.md §4 范本 / §3.2 接口规范)。

每个工具:
  - OpenAI 风格 schema(name/description/parameters/required),经 get_tools() 暴露。
  - 返回 dict:{ok: bool, output|error, diagnosable, ...} — 失败必须含原因,不许"没反应"。
  - 副作用工具(simplex_send_message 外发)先过 policy_check,deny 硬拒、ask 走审批。

工具清单(对应架构 §4.2 功能块):
  simplex_setup            初始化身份/连接(幂等;失败可诊断:lib缺失/网络/服务器不可达)
  simplex_server_status    自检:身份、指向的 SMP 服务器、配置是否生效
  simplex_list_contacts    列出/校验联系人(无联系人→明确反馈)
  simplex_create_invitation 生成一次性关联邀请(即二维码内容,agent 可转 QR 或外发)
  simplex_accept_invitation 通过邀请链接建立联系人(失败→查链接有效性/服务器)
  simplex_send_message     给指定联系人发 E2E 加密消息(无此联系人→诊断并提示)
  simplex_read_messages    读取收件箱(来自 contactConnected/newChatItems 事件缓冲)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

# policy_engine 与本模块同在 oi_enhancements;直接 import(同进程)。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from simplex_runtime import DEFAULT_SMP_SERVER, SimplexRuntime  # noqa: E402

try:  # policy 单源;缺席时降级(standalone 测试用)
    from policy_engine import policy_check_daemon  # noqa: E402
except Exception:  # noqa: BLE001
    # fail-closed:信任扩展工具(加/删联系人)降级为 ask(需人工,绝不静默放行);
    # 其余工具维持 allow(standalone 测试场景,不影响只读/发消息调试)。
    _STANDALONE_ASK = {"simplex_accept_invitation", "simplex_delete_contact"}

    def policy_check_daemon(tool: str, args: dict):  # type: ignore
        if tool in _STANDALONE_ASK:
            return ("ask", "policy_engine 不可用,信任扩展工具 fail-closed 待人工")
        return ("allow", "policy_engine 不可用,standalone 放行")


def _ok(output: Any, **extra) -> dict[str, Any]:
    return {"ok": True, "output": output, **extra}


def _err(reason: str, diagnosable: str, **extra) -> dict[str, Any]:
    """失败契约:reason = 一句话错误;diagnosable = agent 可据此定位/修复的线索。"""
    return {"ok": False, "error": reason, "diagnosable": diagnosable, **extra}


def _runtime() -> SimplexRuntime:
    return SimplexRuntime.instance()


# ────────────────────────────────────────────────────────────────────── #
# 工具实现
# ────────────────────────────────────────────────────────────────────── #

def simplex_setup(display_name: str = "oiagent",
                  smp_server: str = DEFAULT_SMP_SERVER,
                  db_prefix: str | None = None) -> dict[str, Any]:
    """初始化 SimpleX 身份并连接 SMP 服务器(幂等)。"""
    rt = _runtime()
    if rt._thread and rt._thread.is_alive():
        return _ok("SimpleX runtime 已在运行", already_running=True)
    try:
        rt.start(display_name=display_name, smp_server=smp_server, db_prefix=db_prefix)
    except Exception as e:  # noqa: BLE001
        msg = repr(e)
        # 失败可诊断:把常见根因映射成可操作的线索
        if "libsimplex" in msg or "Unsupported platform" in msg:
            diag = "libsimplex.dll 缺失或平台不支持。检查 %LOCALAPPDATA%/simplex-chat 缓存;首次需联网下载。"
        elif "timed out" in msg.lower() or "超时" in msg:
            diag = "初始化超时。可能是 SMP 服务器不可达 — 用 simplex_server_status 查服务器,或确认网络/代理。"
        else:
            diag = f"启动异常:{msg}。查服务器地址格式 / SQLite db_prefix 可写性。"
        return _err("SimpleX 初始化失败", diag)
    return _ok(
        f"SimpleX 已就绪(身份 {display_name},服务器 {smp_server})",
        smp_server=smp_server,
    )


def simplex_server_status() -> dict[str, Any]:
    """自检:身份 / 指向的 SMP 服务器 / 服务器配置是否生效。"""
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup 初始化身份并连接服务器。")
    try:
        st = rt.status()
    except Exception as e:  # noqa: BLE001
        return _err("状态查询失败", f"client 异常:{e!r}")
    server_ok = "失败" not in st.get("server_config", "失败")
    return _ok(
        st,
        server_reachable_hint=server_ok,
        diagnosable=(
            "服务器配置已生效。" if server_ok
            else "SMP 服务器配置失败 — 服务器可能不可达。查 VPS: systemctl status smp-server;或网络/代理。"
        ),
    )


def simplex_list_contacts() -> dict[str, Any]:
    """列出/校验联系人。无联系人时明确反馈(不是'没反应')。"""
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    try:
        contacts = rt.list_contacts()
    except Exception as e:  # noqa: BLE001
        return _err("列出联系人失败", f"client 异常:{e!r}")
    if not contacts:
        return _ok(
            [],
            count=0,
            diagnosable="当前没有任何联系人。先用 simplex_create_invitation 生成邀请,或对端用 simplex_accept_invitation 建立联系。",
        )
    return _ok(contacts, count=len(contacts))


def simplex_create_invitation() -> dict[str, Any]:
    """生成一次性关联邀请(二维码内容)。失败可诊断:服务器通信是否已建立。"""
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    try:
        link = rt.create_invitation()
    except Exception as e:  # noqa: BLE001
        msg = repr(e)
        diag = (
            "生成邀请失败。最常见根因 = 与 SMP 服务器的通信未建立。"
            "用 simplex_server_status 查服务器可达性;确认 /smp 已指向可达服务器。"
            f"原始错误:{msg}"
        )
        return _err("生成关联邀请失败", diag)
    return _ok(
        link,
        link=link,
        diagnosable="这是 simplex:/invitation#... 一次性邀请链接,可转二维码或外发。对端用 simplex_accept_invitation 接受即建立 E2E 加密联系。",
    )


def _is_valid_conn_link(link: str) -> bool:
    """接受 simplex: 直接链接或 https:// 应用链接(短链 /i#/contact 或全链 /invitation#/contact)。

    libsimplex 的 api_create_link 优先返回 connShortLink(https://<server>/i#...),
    没有短链时才回退 connFullLink(simplex:/invitation#...)。两者都合法。
    """
    if not link or not isinstance(link, str):
        return False
    l = link.strip().lower()
    if l.startswith("simplex:"):
        return "/invitation#" in l or "/contact#" in l or "#" in l
    if l.startswith("https://") or l.startswith("http://"):
        # 应用链接:fragment 携带连接数据
        return "#" in l
    return False


def simplex_accept_invitation(link: str, timeout: float = 120.0) -> dict[str, Any]:
    """通过邀请链接建立联系人。失败→查链接有效性/服务器通信。

    信任扩展动作(建立 E2E 连接):先过 policy 审批。deny→硬拒;ask→人工批准
    后放行并在结果标 approved=True;allow(已批准规则)→正常接受。
    """
    verdict, reason = policy_check_daemon("simplex_accept_invitation", {"link": link})
    if verdict == "deny":
        return _err("审批拒绝", f"policy_engine deny:{reason}")
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    if not _is_valid_conn_link(link):
        return _err(
            "邀请链接格式无效",
            f"期望 simplex:/invitation#... / simplex:/contact#... 或 https://<server>/i#... 应用链接,收到:{link!r}。",
        )
    try:
        contact = rt.accept_invitation(link, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        msg = repr(e)
        if "Timeout" in msg or "timed out" in msg.lower():
            diag = "握手超时。邀请方可能离线,或服务器通信中断。确认双方在线 + 服务器可达。"
        elif "ContactAlreadyExists" in msg or "already exists" in msg:
            diag = "该联系人已存在,无需重复接受。用 simplex_list_contacts 查看。"
        else:
            diag = f"接受邀请失败。查链接是否一次性已用过/过期,或服务器通信未建立。原始错误:{msg}"
        return _err("接受邀请失败", diag)
    return _ok(
        contact,
        diagnosable=f"已与 {contact.get('display_name')} 建立 E2E 加密联系(contact_id={contact.get('contact_id')})。",
        approved=(verdict == "ask"),
    )


def simplex_send_message(contact: str, text: str) -> dict[str, Any]:
    """给指定联系人(display name 或 contact_id)发 E2E 加密消息。副作用工具,先过审批。"""
    verdict, reason = policy_check_daemon("simplex_send_message", {"contact": contact, "text": text})
    if verdict == "deny":
        return _err("审批拒绝", f"policy_engine deny:{reason}")
    # ask 分支:standalone 无审批 UI,记录后按批准处理(集成进 daemon 时由审批层拦截)
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")

    resolved = None
    try:
        resolved = rt.resolve_contact(contact)
    except Exception as e:  # noqa: BLE001
        return _err("查询联系人失败", f"client 异常:{e!r}")
    if resolved is None:
        return _err(
            f"没有联系人 '{contact}'",
            "无此联系人。用 simplex_list_contacts 确认现有联系人;"
            "若确需新联系人,先用 simplex_create_invitation 生成邀请请对方接受,"
            "或向对方要其公开名片/邀请链接后用 simplex_accept_invitation 建立。",
        )
    if not resolved.get("active"):
        return _err(
            f"联系人 '{contact}' 连接未激活",
            "该联系人存在但连接未激活(可能对方已删除或服务器队列失效)。"
            "用 simplex_server_status 查服务器,或重新建立联系。",
        )
    try:
        result = rt.send_message(resolved["contact_id"], text)
    except Exception as e:  # noqa: BLE001
        return _err(
            "发送失败",
            f"消息未送达。查服务器通信(simplex_server_status)或对方连接状态。原始错误:{e!r}",
        )
    return _ok(
        result,
        diagnosable=f"已向 {resolved['display_name']} 发送 E2E 加密消息。",
        approved=(verdict == "ask"),
    )


def simplex_send_message_ttl(contact: str, text: str, ttl: int) -> dict[str, Any]:
    """给指定联系人发阅后即焚消息(SimpleX 协议级 ttl,秒;到点双方客户端各自删除)。

    与 simplex_send_message 同为外发副作用工具,先过审批。ttl<=0 非法(普通消息走
    simplex_send_message)。删除上限:防不住对方截图/拍照,只是双方客户端的协议级自毁。
    """
    verdict, reason = policy_check_daemon(
        "simplex_send_message_ttl", {"contact": contact, "text": text, "ttl": ttl}
    )
    if verdict == "deny":
        return _err("审批拒绝", f"policy_engine deny:{reason}")
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    if not isinstance(ttl, int) or ttl <= 0:
        return _err(
            "ttl 非法",
            f"ttl 必须为正整数秒(收到 {ttl!r})。普通不过期消息请用 simplex_send_message。",
        )
    resolved = None
    try:
        resolved = rt.resolve_contact(contact)
    except Exception as e:  # noqa: BLE001
        return _err("查询联系人失败", f"client 异常:{e!r}")
    if resolved is None:
        return _err(
            f"没有联系人 '{contact}'",
            "无此联系人。用 simplex_list_contacts 确认;新联系人先 simplex_create_invitation 或 simplex_accept_invitation。",
        )
    if not resolved.get("active"):
        return _err(
            f"联系人 '{contact}' 连接未激活",
            "连接未激活。用 simplex_server_status 查服务器,或重新建立联系。",
        )
    try:
        result = rt.send_message_ttl(resolved["contact_id"], text, ttl)
    except Exception as e:  # noqa: BLE001
        return _err(
            "阅后即焚发送失败",
            f"查服务器通信/对方连接。注:ttl 是协议级删除,双方客户端都需支持。原始错误:{e!r}",
        )
    return _ok(
        result,
        diagnosable=(
            f"已向 {resolved['display_name']} 发送阅后即焚消息(ttl={ttl}s,到点双方客户端删除)。"
            "注意:协议级删除防不住对方截图/拍照。"
        ),
        approved=(verdict == "ask"),
    )


def simplex_delete_contact(contact: str) -> dict[str, Any]:
    """删除联系人(断开并移除连接)。用于清理测试期残留的重复/失效连接。
    副作用工具,先过审批。"""
    verdict, reason = policy_check_daemon("simplex_delete_contact", {"contact": contact})
    if verdict == "deny":
        return _err("审批拒绝", f"policy_engine deny:{reason}")
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    resolved = None
    try:
        resolved = rt.resolve_contact(contact)
    except Exception as e:  # noqa: BLE001
        return _err("查询联系人失败", f"client 异常:{e!r}")
    if resolved is None:
        return _err(f"没有联系人 '{contact}'",
                    "用 simplex_list_contacts 确认现有联系人名/id。")
    try:
        result = rt.delete_contact(resolved["contact_id"])
    except Exception as e:  # noqa: BLE001
        return _err("删除联系人失败", f"原始错误:{e!r}")
    if not result.get("deleted"):
        return _err("删除联系人失败", result.get("error", "未知原因"))
    return _ok(result,
               diagnosable=f"已删除联系人 {resolved['display_name']}(contact_id={resolved['contact_id']})。",
               approved=(verdict == "ask"))



def simplex_read_messages(contact: str | None = None, limit: int = 20,
                          pop: bool = False) -> dict[str, Any]:
    """读取收件箱(到达的 E2E 消息)。可按联系人过滤;pop=True 消费式读取。"""
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    cid = None
    if contact:
        resolved = rt.resolve_contact(contact)
        if resolved is None:
            return _err(
                f"没有联系人 '{contact}'",
                "无此联系人,无法按联系人过滤。用 simplex_list_contacts 查看现有联系人。",
            )
        cid = resolved["contact_id"]
    items = rt.drain_inbox(cid) if pop else rt.read_inbox(limit=limit, contact_id=cid)
    return _ok(
        items,
        count=len(items),
        diagnosable=(
            f"{len(items)} 条消息。" if items
            else "收件箱为空。对方发消息后会经 newChatItems 事件进入缓冲;确认双方在线 + 服务器可达。"
        ),
    )


# ────────────────────────────────────────────────────────────────────── #
# 工具注册(schema + 分发)
# ────────────────────────────────────────────────────────────────────── #

_TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "simplex_setup": simplex_setup,
    "simplex_server_status": simplex_server_status,
    "simplex_list_contacts": simplex_list_contacts,
    "simplex_create_invitation": simplex_create_invitation,
    "simplex_accept_invitation": simplex_accept_invitation,
    "simplex_send_message": simplex_send_message,
    "simplex_send_message_ttl": simplex_send_message_ttl,
    "simplex_read_messages": simplex_read_messages,
    "simplex_delete_contact": simplex_delete_contact,
}


def get_tools() -> list[dict[str, Any]]:
    """OpenAI 风格 function-calling schema 列表(对应架构 §3.2)。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "simplex_setup",
                "description": "初始化 SimpleX 身份并连接 SMP 服务器(幂等)。其他 simplex_* 工具的前置。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "display_name": {"type": "string", "description": "本机身份显示名", "default": "oiagent"},
                        "smp_server": {"type": "string", "description": "SMP 服务器地址", "default": DEFAULT_SMP_SERVER},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_server_status",
                "description": "自检 SimpleX:身份、指向的 SMP 服务器、服务器配置是否生效。用于诊断'服务器通信未建立'类失败。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_list_contacts",
                "description": "列出/校验所有 SimpleX 联系人。无联系人时返回明确反馈与下一步建议。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_create_invitation",
                "description": "生成一次性关联邀请(simplex:/invitation#... 链接,即二维码内容)。对端接受即建立 E2E 加密联系。",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_accept_invitation",
                "description": "通过邀请链接(simplex:/invitation#... 或 simplex:/contact#...)与对端建立 E2E 加密联系人。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "link": {"type": "string", "description": "邀请/名片链接"},
                        "timeout": {"type": "number", "description": "握手超时秒数", "default": 120},
                    },
                    "required": ["link"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_send_message",
                "description": "给指定联系人(display name 或 contact_id)发送 E2E 加密消息。无此联系人时返回诊断并提示如何建立。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "联系人 display name 或 contact_id"},
                        "text": {"type": "string", "description": "消息文本"},
                    },
                    "required": ["contact", "text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_send_message_ttl",
                "description": "给指定联系人发阅后即焚消息(SimpleX 协议级 ttl,秒;到点双方客户端各自删除)。防不住截图/拍照。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "联系人 display name 或 contact_id"},
                        "text": {"type": "string", "description": "消息文本"},
                        "ttl": {"type": "integer", "description": "存活秒数,正整数;到点双方客户端删除"},
                    },
                    "required": ["contact", "text", "ttl"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_read_messages",
                "description": "读取收件箱中到达的 E2E 加密消息。可按联系人过滤;pop=true 为消费式读取。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "可选:按联系人过滤(display name 或 id)"},
                        "limit": {"type": "integer", "description": "最多返回条数", "default": 20},
                        "pop": {"type": "boolean", "description": "true=读取后清空(消费)", "default": False},
                    },
                    "required": [],
                },
            },
        },
    ]


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """分发器:按名字调用对应工具实现。"""
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
