"""simplex_files.py — Agent-First OS L2 功能块①:SimpleX 语音/文件发送(XFTP)

设计依据:Documents/prisiragent-os-integration/capability-01-voice-file-send.md。
绕开官方缺失的 apiSendFile:文件作为 ComposedMessage.fileSource 随 /_send 发出,
libsimplex 自动加密 + XFTP 上传(enableSndFiles=True 已开),并监听
sndFileCompleteXFTP / sndFileError 确认送达。

每个工具返回 {ok, output|error, diagnosable} — 失败必须含可定位修复的线索。
发送是外发副作用,先过 policy_check_daemon,deny 硬拒、ask 走审批。

安全红线(本块特有攻击面):只能发送白名单根目录内的文件,防 agent 把任意系统
文件外发。默认允许 aureon 数据目录与系统临时目录;经 SIMPLEX_SEND_ROOTS 扩展。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from simplex_runtime import SimplexRuntime  # noqa: E402

try:  # policy 单源;缺席时降级为全 allow(standalone 测试用)
    from policy_engine import policy_check_daemon  # noqa: E402
except Exception:  # noqa: BLE001
    def policy_check_daemon(tool: str, args: dict):  # type: ignore
        return ("allow", "policy_engine 不可用,standalone 放行")


# 音频扩展名(send_voice 校验用)
_AUDIO_EXTS = {".m4a", ".aac", ".ogg", ".opus", ".mp3", ".wav", ".flac"}
# 语音默认时长上限(秒),防误发整段录音
_VOICE_MAX_DURATION = 300
# 可发送的根目录白名单(aureon 数据目录 + 系统临时目录)
_DEFAULT_ROOTS = [
    Path.home() / ".local" / "share" / "aureon",
    Path(tempfile.gettempdir()),
]


def _ok(output: Any, **extra) -> dict[str, Any]:
    return {"ok": True, "output": output, **extra}


def _err(reason: str, diagnosable: str, **extra) -> dict[str, Any]:
    return {"ok": False, "error": reason, "diagnosable": diagnosable, **extra}


def _runtime() -> SimplexRuntime:
    return SimplexRuntime.instance()


def _allowed_roots() -> list[Path]:
    roots = list(_DEFAULT_ROOTS)
    extra = os.environ.get("SIMPLEX_SEND_ROOTS", "")
    for part in extra.split(os.pathsep):
        if part.strip():
            roots.append(Path(part.strip()))
    return roots


def register_send_root(path: str) -> None:
    """把一个文件所在目录并入本进程的 SIMPLEX_SEND_ROOTS(壳选文件后调用)。
    仅当前进程有效,不写盘、不改系统环境;保证用户亲手选的文件可发,
    又不永久放宽白名单。"""
    p = Path(path).expanduser()
    d = p if p.is_dir() else p.parent
    cur = os.environ.get("SIMPLEX_SEND_ROOTS", "")
    parts = [x for x in cur.split(os.pathsep) if x.strip()]
    if str(d) not in parts:
        parts.append(str(d))
        os.environ["SIMPLEX_SEND_ROOTS"] = os.pathsep.join(parts)


def _resolve_sendable(path: str) -> tuple[Path | None, dict[str, Any] | None]:
    """校验路径:存在、是文件、在白名单根目录内。返回 (resolved_path, error_dict)。"""
    if not path or not isinstance(path, str):
        return None, _err("路径为空", "提供要发送的本地文件绝对路径。")
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path.cwd() / p
    try:
        p = p.resolve()
    except Exception as e:  # noqa: BLE001
        return None, _err("路径解析失败", f"{e!r}")
    if not p.exists():
        return None, _err("文件不存在", f"路径 {p} 不存在。确认文件已生成在本机、路径拼写正确。")
    if not p.is_file():
        return None, _err("不是文件", f"路径 {p} 不是常规文件(可能是目录)。提供具体文件路径。")
    roots = _allowed_roots()
    if not any(p.is_relative_to(r.resolve()) for r in roots if r.exists()):
        return None, _err(
            "路径不在允许范围",
            "为防数据外泄,只能发送白名单根目录内的文件:"
            + ", ".join(str(r) for r in roots)
            + "。把文件放进其中之一,或设 SIMPLEX_SEND_ROOTS 扩展允许目录。",
        )
    return p, None


def _pre_send(contact: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """发送前公共检查:policy 审批 + runtime 运行 + 联系人解析。
    返回 (resolved_contact, error_dict);成功时 error_dict 为 None。"""
    verdict, reason = policy_check_daemon("simplex_send_file", {"contact": contact})
    if verdict == "deny":
        return None, _err("审批拒绝", f"policy_engine deny:{reason}")
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return None, _err("SimpleX runtime 未启动", "先调 simplex_setup 初始化身份并连接服务器。")
    try:
        resolved = rt.resolve_contact(contact)
    except Exception as e:  # noqa: BLE001
        return None, _err("查询联系人失败", f"client 异常:{e!r}")
    if resolved is None:
        return None, _err(
            f"没有联系人 '{contact}'",
            "无此联系人。用 simplex_list_contacts 确认;新联系人先 simplex_create_invitation 或 simplex_accept_invitation。",
        )
    if not resolved.get("active"):
        return None, _err(
            f"联系人 '{contact}' 连接未激活",
            "连接未激活(对方删除或队列失效)。用 simplex_server_status 查服务器,或重新建立联系。",
        )
    resolved["_approved"] = (verdict == "ask")
    return resolved, None


def _report_send(result: dict[str, Any], resolved: dict[str, Any], kind: str) -> dict[str, Any]:
    """把 runtime 的 send_file 结果映射成最终工具返回(含可诊断信息)。"""
    status = result.get("status")
    name = resolved["display_name"]
    if status == "error":
        return _err(
            "文件发送失败(XFTP)",
            f"已入队但 XFTP 上传出错:{result.get('xftp_error')}。"
            "查 XFTP 服务器可达性(自建 xftp-server)与对方连接状态。",
        )
    extra: dict[str, Any] = {"approved": resolved.get("_approved", False)}
    if status == "delivered":
        return _ok(result, diagnosable=f"已向 {name} 发送{kind}并经 XFTP 确认送达。", **extra)
    # sent:发出即返回(不阻塞 actor 循环等送达);真实送达由接收端确认
    return _ok(
        result,
        diagnosable=(
            f"已向 {name} 发出{kind}(file_id={result.get('file_id')})。"
            "XFTP 分片在后台异步上传;对方接收后即完成投递。"
        ),
        **extra,
    )


def simplex_send_file(contact: str, path: str, caption: str = "", timeout: float = 120.0) -> dict[str, Any]:
    """给指定联系人发送任意文件,E2E 加密经 XFTP 投递。"""
    resolved, err = _pre_send(contact)
    if err:
        return err
    p, perr = _resolve_sendable(path)
    if perr:
        return perr
    try:
        result = _runtime().send_file(
            resolved["contact_id"], str(p), caption, kind="file", timeout=timeout
        )
    except Exception as e:  # noqa: BLE001
        msg = repr(e)
        if "fileNotFound" in msg:
            diag = "libsimplex 报 fileNotFound — 路径对客户端进程不可见(权限/沙箱)。确认 daemon 进程能读该文件。"
        elif "fileSize" in msg:
            diag = "文件超出大小限制。大文件考虑压缩或分卷。"
        else:
            diag = f"发送异常。查服务器通信(simplex_server_status)与 XFTP 服务器。原始错误:{msg}"
        return _err("发送失败", diag)
    return _report_send(result, resolved, "文件")


def simplex_send_voice(contact: str, path: str, caption: str = "",
                       duration: int | None = None, timeout: float = 120.0) -> dict[str, Any]:
    """给指定联系人发送语音消息(send_file 的语义化封装,校验音频格式/时长)。"""
    resolved, err = _pre_send(contact)
    if err:
        return err
    p, perr = _resolve_sendable(path)
    if perr:
        return perr
    if p.suffix.lower() not in _AUDIO_EXTS:
        return _err(
            f"非音频文件 ({p.suffix})",
            f"simplex_send_voice 期望音频({sorted(_AUDIO_EXTS)})。若要发任意文件用 simplex_send_file。",
        )
    if duration is not None and duration > _VOICE_MAX_DURATION:
        return _err(
            f"语音超长 ({duration}s)",
            f"超过 {_VOICE_MAX_DURATION}s 上限,防误发整段录音。确认无误后用 simplex_send_file 发送。",
        )
    cap = caption or (f"[voice] {duration}s" if duration else "[voice]")
    try:
        result = _runtime().send_file(
            resolved["contact_id"], str(p), cap, kind="voice", duration=duration, timeout=timeout
        )
    except Exception as e:  # noqa: BLE001
        return _err("发送失败", f"语音发送异常。查服务器/ XFTP。原始错误:{e!r}")
    return _report_send(result, resolved, "语音")


def simplex_receive_file(file_id: int, timeout: float = 90.0) -> dict[str, Any]:
    """接受并下载一个到达的文件邀请(bot 收到文件后需显式下载)。

    下载是异步的:接受后在工具层轮询 poll_receive_file(不阻塞 actor 事件循环,
    该循环要推进 XFTP 下载 worker),直到完成或超时。"""
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    try:
        acc = rt.receive_file(file_id)
    except Exception as e:  # noqa: BLE001
        return _err("下载接受失败", f"receive_file 异常。查 file_id 是否有效/对方是否已取消。原始错误:{e!r}")
    if acc.get("status") != "receiving":
        return _err("下载未被接受", f"{acc}。查 file_id 与 XFTP 服务器。")

    # 工具层轮询完成(不占用 actor 循环)
    deadline = time.time() + timeout
    while time.time() < deadline:
        st = rt.poll_receive_file(file_id)
        if st.get("done"):
            if st.get("ok"):
                return _ok(st, diagnosable=f"文件已下载到 {st.get('saved_path')}。")
            return _err("下载失败", f"{st.get('error')}。查 XFTP 服务器可达性与对方连接。")
        time.sleep(1.0)
    return _err(
        "下载超时未完成",
        f"{timeout}s 内未收到 rcvFileComplete。大文件/网络慢常见;文件仍在下载,稍后可用 simplex_receive_file 重查或增大 timeout。",
    )


def simplex_list_incoming_files(contact: str | None = None) -> dict[str, Any]:
    """列出缓冲的待下载文件邀请(file_id 供 simplex_receive_file 使用)。"""
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    cid = None
    if contact:
        resolved = rt.resolve_contact(contact)
        if resolved is None:
            return _err(f"没有联系人 '{contact}'", "用 simplex_list_contacts 查看现有联系人。")
        cid = resolved["contact_id"]
    items = rt.list_inbox_files(cid)
    return _ok(
        items,
        count=len(items),
        diagnosable=(
            f"{len(items)} 个待下载文件,用 simplex_receive_file(file_id) 下载。"
            if items else "暂无待下载文件。对方发文件后会以 fileInvitation 进入缓冲。"
        ),
    )


# ────────────────────────────────────────────────────────────────────── #
# 工具注册(schema + 分发)
# ────────────────────────────────────────────────────────────────────── #

_TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "simplex_send_file": simplex_send_file,
    "simplex_send_voice": simplex_send_voice,
    "simplex_receive_file": simplex_receive_file,
    "simplex_list_incoming_files": simplex_list_incoming_files,
}


def get_tools() -> list[dict[str, Any]]:
    """OpenAI 风格 function-calling schema 列表(对应架构 §3.2)。"""
    return [
        {
            "type": "function",
            "function": {
                "name": "simplex_send_file",
                "description": "给指定联系人发送任意文件(文档/图片/任意二进制),E2E 加密经 XFTP 投递并确认送达。仅能发送白名单目录内的文件。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "联系人 display name 或 contact_id"},
                        "path": {"type": "string", "description": "本地文件绝对路径(须在允许的目录内)"},
                        "caption": {"type": "string", "description": "随文件的文本说明", "default": ""},
                        "timeout": {"type": "number", "description": "等待 XFTP 送达确认秒数", "default": 120},
                    },
                    "required": ["contact", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_send_voice",
                "description": "给指定联系人发送语音消息(校验音频格式与时长),E2E 加密经 XFTP 投递并确认送达。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "联系人 display name 或 contact_id"},
                        "path": {"type": "string", "description": "本地音频文件路径(m4a/aac/ogg/opus/mp3/wav/flac)"},
                        "caption": {"type": "string", "description": "随语音的文本说明", "default": ""},
                        "duration": {"type": "integer", "description": "语音时长秒数(可选,>300s 会被拒)"},
                        "timeout": {"type": "number", "description": "等待 XFTP 送达确认秒数", "default": 120},
                    },
                    "required": ["contact", "path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_receive_file",
                "description": "接受并下载一个到达的文件邀请(file_id 来自 simplex_list_incoming_files)。bot 收到文件后需显式下载。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "file_id": {"type": "integer", "description": "待下载文件的 fileId"},
                    },
                    "required": ["file_id"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "simplex_list_incoming_files",
                "description": "列出缓冲的待下载文件邀请(file_id 供 simplex_receive_file 下载)。可按联系人过滤。",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string", "description": "可选:按联系人过滤(display name 或 id)"},
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
