"""simplex_runtime.py — Agent-First OS L2 功能块:SimpleX 协议直连运行时

阶段 1(SimpleX 协议功能块化)的核心运行时。把 simplex-chat 的 Python 客户端
(`simplex_chat`,in-process FFI 驱动 libsimplex.dll)包成**单实例、后台常驻、
同步可调用**的能力集,供 OIagent / 对话界面以工具形式调用。

设计约束(对齐 agent-first-os-architecture.md §3.2 / §4):
  - 每个功能块 = 明确的输入→输出契约,返回 {ok: bool, output|error, diagnosable}。
  - 失败必须可诊断:返回原因(服务器未连接/无此联系人/链接无效/超时),不许"没反应"。
  - 面向 agent:联系人以 display name 或 contactId 引用,agent 不用管 ConnId/队列。

并发模型(借鉴 mcp_client_bridge.py 的 actor 模型):
  - libsimplex 客户端是 async + 单事件循环亲和的;不能跨线程拆 future。
  - 故整个 client 生命周期放在一个**专属后台线程的事件循环**里(actor);
    同步侧经 run_coroutine_threadsafe 提交协程并拿结果。
  - 接收循环(serve_forever)常驻,驱动 connect_to/send_and_wait 的 waiter,
    并把到达的消息缓冲进收件箱供 simplex_read_messages 拉取。

一次只服务一个 SimpleX 身份(单 profile)。这是能力块,不是多账户 IM。
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

# 默认自建 SMP 服务器(VPS,postgres 后端;见 simplex-selfhosted-server-2026-07-19)
DEFAULT_SMP_SERVER = (
    "smp://JL3cUrxYU15qFNtHnsFNUfeZR0l3iP-CH7Jd3SrlLjk=@192.220.14.165:5223"
)
# 默认自建 XFTP 服务器(文件分片存储;同 VPS 8443)。文件发送必须配它,
# 否则 libsimplex 默认走公共 XFTP,自建内网下分片不互通 → 接收方拉不到。
DEFAULT_XFTP_SERVER = (
    "xftp://aVv9lQsIp3xNEyBWBjg_bQKlRl-fQ13xirC9EIVYark=@192.220.14.165:8443"
)

# 默认身份数据库位置(与 policy_engine 同区,落在 aureon 数据目录)
_DEFAULT_DB_DIR = Path.home() / ".local" / "share" / "aureon" / "simplex"


@dataclass
class InboxItem:
    """一条到达的消息(收件箱缓冲,供 simplex_read_messages 拉取)。"""

    contact_id: int
    contact_name: str
    text: str
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contact_id": self.contact_id,
            "contact_name": self.contact_name,
            "text": self.text,
            "ts": self.ts,
        }


@dataclass
class InboxFileItem:
    """一个到达的文件邀请(缓冲,供 simplex_receive_file 显式下载)。"""

    file_id: int
    contact_id: int
    contact_name: str
    file_name: str
    file_size: int
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_id": self.file_id,
            "contact_id": self.contact_id,
            "contact_name": self.contact_name,
            "file_name": self.file_name,
            "file_size": self.file_size,
            "ts": self.ts,
        }


class SimplexRuntime:
    """SimpleX 能力的单例运行时(actor 模型,后台事件循环)。"""

    _instance: Optional["SimplexRuntime"] = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Any = None  # simplex_chat.Client
        self._serve_task: Optional[asyncio.Task] = None
        self._ready = threading.Event()
        self._init_error: Optional[str] = None
        self._inbox: list[InboxItem] = []
        self._inbox_lock = threading.Lock()
        self._inbox_files: list[InboxFileItem] = []
        self._file_download_dir: str = str(_DEFAULT_DB_DIR / "downloads")
        # 文件下载 watcher/done(file_id -> 状态),事件驱动,非阻塞(见 _a_receive_file)
        self._rcv_watchers: dict[int, bool] = {}
        self._rcv_done: dict[int, dict[str, Any]] = {}
        # A2H 审批:approver 的 contact_id + 待裁决 waiter(request_id -> asyncio.Future)
        self._a2h_approver_cid: Optional[int] = None
        self._a2h_approver_name: Optional[str] = None
        self._a2h_waiters: dict[str, Any] = {}  # request_id -> Future(bool)
        self._a2h_pending: dict[str, dict[str, Any]] = {}  # request_id -> {action, reason, ts}
        self._smp_server: str = DEFAULT_SMP_SERVER
        self._display_name: str = "oiagent"
        self._db_prefix: str = str(_DEFAULT_DB_DIR / "oiagent_simplex")

    # ------------------------------------------------------------------ #
    # 单例
    # ------------------------------------------------------------------ #
    @classmethod
    def instance(cls) -> "SimplexRuntime":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #
    def start(
        self,
        display_name: str = "oiagent",
        smp_server: str = DEFAULT_SMP_SERVER,
        db_prefix: Optional[str] = None,
    ) -> None:
        """启动后台事件循环并在其中初始化 SimpleX client(幂等)。"""
        if self._thread and self._thread.is_alive():
            return
        self._display_name = display_name
        self._smp_server = smp_server
        if db_prefix:
            self._db_prefix = db_prefix
        Path(self._db_prefix).parent.mkdir(parents=True, exist_ok=True)

        self._ready.clear()
        self._init_error = None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, name="simplex-actor", daemon=True
        )
        self._thread.start()
        # 等待 actor 内的 client 初始化完成(或报错)
        if not self._ready.wait(timeout=120):
            raise RuntimeError("SimpleX runtime 初始化超时(120s)")
        if self._init_error:
            raise RuntimeError(self._init_error)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._boot())
        except Exception as e:  # noqa: BLE001
            self._init_error = f"SimpleX runtime 启动失败: {e!r}"
            self._ready.set()
            return
        self._loop.run_forever()

    async def _boot(self) -> None:
        """在 actor 线程内初始化 client 并常驻接收循环。"""
        from simplex_chat import Client, Profile, SqliteDb

        # 文件下载前置:libsimplex 的接收/上传 worker 需要 temp/files 目录,
        # 而 Python 客户端 start_chat 只发裸 /_start,从不设这两个目录(Haskell
        # 端要求 StartChat 前 /_temp_folder + /_files_folder,否则下载静默失败)。
        # 故 patch start_chat:在 /_start 之前先发这两条目录命令。
        files_root = Path(self._db_prefix).parent / "simplex_files_root"
        tmp_dir = files_root / "tmp"
        dl_dir = files_root / "downloads"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        dl_dir.mkdir(parents=True, exist_ok=True)
        self._file_download_dir = str(dl_dir)

        from simplex_chat.api import ChatApi as _ChatApi

        _orig_start_chat = _ChatApi.start_chat

        async def _start_chat_with_folders(api_self):  # noqa: ANN001
            try:
                await api_self.send_chat_cmd(f"/_temp_folder {tmp_dir}")
                await api_self.send_chat_cmd(f"/_files_folder {dl_dir}")
            except Exception:  # noqa: BLE001
                pass  # 目录命令失败不阻断启动(诊断时仍可见下载失败)
            await _orig_start_chat(api_self)

        _ChatApi.start_chat = _start_chat_with_folders

        client = Client(
            profile=Profile(display_name=self._display_name),
            db=SqliteDb(file_prefix=self._db_prefix),
            log_contacts=True,
        )
        await client.__aenter__()
        self._client = client

        # 把到达的消息缓冲进收件箱(text 类型;其余类型记占位)
        @client.on_message(content_type="text")
        async def _capture(msg):  # noqa: ANN001
            ci = msg.chat_item
            chat_info = ci.get("chatInfo", {})
            contact = chat_info.get("contact", {}) if chat_info.get("type") == "direct" else {}
            cid = contact.get("contactId")
            cname = contact.get("localDisplayName") or contact.get("profile", {}).get(
                "displayName", "?"
            )
            if cid is not None:
                # A2H 审批裁决识别:仅认 approver 的 "yes/no <request_id>" 回复,优先于收件箱
                text = msg.text or ""
                if self._a2h_approver_cid is not None and cid == self._a2h_approver_cid:
                    self._a2h_maybe_resolve(text)
                with self._inbox_lock:
                    self._inbox.append(
                        InboxItem(contact_id=cid, contact_name=cname, text=text)
                    )

        # 捕获到达的文件邀请(file/voice/image/video 消息),缓冲 file_id 供显式下载
        @client.on_message(content_type="file")
        @client.on_message(content_type="voice")
        @client.on_message(content_type="image")
        @client.on_message(content_type="video")
        async def _capture_file(msg):  # noqa: ANN001
            ci = msg.chat_item
            chat_info = ci.get("chatInfo", {})
            contact = chat_info.get("contact", {}) if chat_info.get("type") == "direct" else {}
            cid = contact.get("contactId")
            cname = contact.get("localDisplayName") or contact.get("profile", {}).get(
                "displayName", "?"
            )
            finfo = (ci.get("chatItem", {}) or {}).get("file", {}) or {}
            fid = finfo.get("fileId")
            if cid is not None and fid is not None:
                with self._inbox_lock:
                    self._inbox_files.append(
                        InboxFileItem(
                            file_id=fid,
                            contact_id=cid,
                            contact_name=cname,
                            file_name=finfo.get("fileName", "?"),
                            file_size=finfo.get("fileSize", 0),
                        )
                    )

        # 应用自建服务器(尽力而为;失败不阻断启动,诊断时报告)
        self._server_config_note = await self._apply_server_config()

        # 常驻接收循环(驱动 waiter + 收件箱)
        self._serve_task = asyncio.create_task(client.serve_forever())
        self._ready.set()

    async def _apply_server_config(self) -> str:
        """用 CLI 语法 /smp + /xftp 指向自建服务器。返回诊断备注。"""
        notes = []
        try:
            r = await self._client.api.send_chat_cmd(f"/smp {self._smp_server}")
            notes.append(f"/smp -> {r.get('type', '?')}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"/smp 配置失败: {e!r}")
        try:
            r = await self._client.api.send_chat_cmd(f"/xftp {DEFAULT_XFTP_SERVER}")
            notes.append(f"/xftp -> {r.get('type', '?')}")
        except Exception as e:  # noqa: BLE001
            notes.append(f"/xftp 配置失败: {e!r}")
        return "; ".join(notes)

    # ------------------------------------------------------------------ #
    # 同步提交入口
    # ------------------------------------------------------------------ #
    def _submit(self, coro, timeout: float = 90.0) -> Any:
        if not (self._loop and self._thread and self._thread.is_alive()):
            raise RuntimeError("SimpleX runtime 未启动 — 先调 simplex_setup")
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def _require_client(self) -> Any:
        if self._client is None:
            raise RuntimeError("SimpleX client 未初始化 — 先调 simplex_setup")
        return self._client

    async def _a_chat_items(self, contact_id: int, limit: int = 60) -> list[dict[str, Any]]:
        """读该联系人的完整对话历史(带方向 me/them),按时间升序。

        关键:不能用 api_get_chats —— 那是"会话列表"查询,每个会话只带**最新一条**消息
        (且 query=filters 是会话过滤器不是消息范围)。要拿完整历史必须用原始命令
        `/_get chat @<contactId> count=N`(每会话的消息列表)。
        返回 [{dir:'me'|'them', text, ts, kind}]。"""
        api = self._require_client().api
        items: list[dict[str, Any]] = []
        try:
            r = await api.send_chat_cmd(f"/_get chat @{contact_id} count={max(limit, 60)}")
        except Exception:  # noqa: BLE001
            return []
        chat = r.get("chat", {})
        for item in chat.get("chatItems", []):
            cd = (item.get("chatDir", {}) or {}).get("type", "")
            direction = "me" if cd == "directSnd" else "them"
            meta = item.get("meta", {}) or {}
            text = meta.get("itemText", "")
            ts = meta.get("itemTs", "")
            item_id = meta.get("itemId")  # 服务端稳定 id,供前端 reconcile(防闪烁)
            content = item.get("content", {}) or {}
            ctype = content.get("type", "")
            if text.startswith("[SIGMANIFEST]"):
                kind = "manifest"
            elif ctype in ("rcvMsgContent", "sndMsgContent"):
                mc = content.get("msgContent")
                kind = (mc.get("type") if isinstance(mc, dict) else "text") or "text"
            else:
                kind = "other"
            if not text:
                continue
            items.append({"id": item_id, "dir": direction, "text": text, "ts": ts, "kind": kind})
        items.sort(key=lambda x: x.get("ts", ""))
        return items[-limit:]

    def chat_items(self, contact_id: int, limit: int = 60) -> list[dict[str, Any]]:
        return self._submit(self._a_chat_items(contact_id, limit))

    async def _a_chat_texts(self, contact_id: int, limit: int = 50) -> list[str]:
        """从 libsimplex 聊天存储读该联系人的最近文本消息(持久,不依赖内存收件箱)。

        与 read_inbox 的差别:收件箱是进程内存(重启即空,且只见运行期新消息);
        这里走 api_get_chats 查持久聊天历史,跨进程/重启可见。供签名清单等需要
        回溯消息的功能用。"""
        api = self._require_client().api
        user = await api.api_get_active_user()
        if not user:
            return []
        chats = await api.api_get_chats(user["userId"], {"count": limit})
        texts: list[str] = []
        for ch in chats:
            info = ch.get("chatInfo", {})
            if info.get("type") != "direct":
                continue
            if info.get("contact", {}).get("contactId") != contact_id:
                continue
            for item in ch.get("chatItems", []):
                # item 结构:{chatDir, meta:{itemText,...}, content:{type, msgContent?}, ...}
                # 文本消息正文最可靠取 meta.itemText;content.msgContent.text 作补充。
                meta_txt = (item.get("meta", {}) or {}).get("itemText")
                if meta_txt:
                    texts.append(meta_txt)
                    continue
                content = item.get("content", {}) or {}
                mc = content.get("msgContent")
                if isinstance(mc, dict) and mc.get("type") == "text":
                    texts.append(mc.get("text", ""))
        return texts

    def chat_texts(self, contact_id: int, limit: int = 50) -> list[str]:
        return self._submit(self._a_chat_texts(contact_id, limit))

    # ------------------------------------------------------------------ #
    # 能力实现(actor 内执行)
    # ------------------------------------------------------------------ #
    async def _a_status(self) -> dict[str, Any]:
        api = self._require_client().api
        user = await api.api_get_active_user()
        note = getattr(self, "_server_config_note", "?")
        return {
            "active_user": (user or {}).get("profile", {}).get("displayName"),
            "user_id": (user or {}).get("userId"),
            "smp_server": self._smp_server,
            "server_config": note,
            "chat_started": api.started,
        }

    def status(self) -> dict[str, Any]:
        return self._submit(self._a_status())

    async def _a_list_contacts(self) -> list[dict[str, Any]]:
        api = self._require_client().api
        user = await api.api_get_active_user()
        if not user:
            raise RuntimeError("无 active user")
        contacts = await api.api_list_contacts(user["userId"])
        out = []
        for c in contacts:
            conn = c.get("activeConn") or {}
            out.append(
                {
                    "contact_id": c.get("contactId"),
                    "display_name": c.get("localDisplayName")
                    or c.get("profile", {}).get("displayName"),
                    # 跨端稳定的对方 profile 显示名(信令房间身份源),不受本地备注影响。
                    # UI 显示仍用 display_name(本地备注);仅 WS 房间路由用 peer_profile_name。
                    "peer_profile_name": c.get("profile", {}).get("displayName"),
                    "active": bool(c.get("activeConn")),
                    # 文件发送要求 connStatus=ready(accepted/sndReady 阶段会 contactNotReady)
                    "conn_status": (conn.get("connStatus") or {}).get("type"),
                    "ready": (conn.get("connStatus") or {}).get("type") == "ready",
                }
            )
        return out

    def list_contacts(self) -> list[dict[str, Any]]:
        return self._submit(self._a_list_contacts())

    async def _a_wait_contact_ready(self, contact_id: int, timeout: float) -> bool:
        """轮询直到该联系人 connStatus=ready(文件发送前置;contactNotReady 的根因)。"""
        api = self._require_client().api
        user = await api.api_get_active_user()
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            contacts = await api.api_list_contacts(user["userId"])
            for c in contacts:
                if c.get("contactId") == contact_id:
                    st = ((c.get("activeConn") or {}).get("connStatus") or {}).get("type")
                    if st == "ready":
                        return True
            await asyncio.sleep(1.0)
        return False

    def wait_contact_ready(self, contact_id: int, timeout: float = 60.0) -> bool:
        return self._submit(self._a_wait_contact_ready(contact_id, timeout), timeout=timeout + 15)

    async def _a_create_invitation(self) -> str:
        api = self._require_client().api
        user = await api.api_get_active_user()
        if not user:
            raise RuntimeError("无 active user")
        return await api.api_create_link(user["userId"])

    def create_invitation(self) -> str:
        return self._submit(self._a_create_invitation())

    async def _a_accept(self, link: str, timeout: float) -> dict[str, Any]:
        client = self._require_client()
        contact = await client.connect_to(link, timeout=timeout)
        return {
            "contact_id": contact.get("contactId"),
            "display_name": contact.get("localDisplayName")
            or contact.get("profile", {}).get("displayName"),
        }

    def accept_invitation(self, link: str, timeout: float = 120.0) -> dict[str, Any]:
        return self._submit(self._a_accept(link, timeout), timeout=timeout + 20)

    async def _a_send(self, contact_id: int, text: str) -> dict[str, Any]:
        api = self._require_client().api
        items = await api.api_send_text_message(["direct", contact_id], text)
        return {"sent_items": len(items), "contact_id": contact_id, "text": text}

    def send_message(self, contact_id: int, text: str) -> dict[str, Any]:
        return self._submit(self._a_send(contact_id, text))

    async def _a_delete_contact(self, contact_id: int) -> dict[str, Any]:
        """删除联系人(断开并移除连接)。用于清理测试期残留的重复连接。
        SimpleX API 命令: /_delete @<contactId>。"""
        api = self._require_client().api
        try:
            r = await api.send_chat_cmd(f"/_delete @{contact_id}")
        except Exception as e:  # noqa: BLE001
            return {"deleted": False, "contact_id": contact_id, "error": repr(e)}
        # r 通常含 contact 被删的信息;无异常即视为成功
        return {"deleted": True, "contact_id": contact_id, "resp_type": r.get("type")}

    def delete_contact(self, contact_id: int) -> dict[str, Any]:
        return self._submit(self._a_delete_contact(contact_id))

    # ------------------------------------------------------------------ #
    # 文件/语音发送(功能块①:fileSource 随 /_send 发出,enableSndFiles=True)
    # ------------------------------------------------------------------ #
    def _cid_from_chat_item(self, item: Any) -> Optional[int]:
        """从 AChatItem 提取 direct contactId;非 direct 返回 None。"""
        try:
            info = item.get("chatInfo", {})
            if info.get("type") == "direct":
                return info.get("contact", {}).get("contactId")
        except AttributeError:
            pass
        return None

    async def _a_send_file(
        self,
        contact_id: int,
        path: str,
        caption: str,
        kind: str,
        duration: Optional[int],
        timeout: float,
    ) -> dict[str, Any]:
        """构造带 fileSource 的 ComposedMessage 经 api_send_messages 发出(libsimplex
        自动加密 + XFTP 上传),并阻塞等 sndFileCompleteXFTP / sndFileError 确认送达。"""
        api = self._require_client().api

        # 文件发送要求 connStatus=ready;刚 accept 完是 accepted/sndReady,直接发会
        # contactNotReady。先等连接 ready(文本消息无此要求,故 send_message 不等)。
        await self._a_wait_contact_ready(contact_id, timeout=min(30.0, timeout))

        msg_content: dict[str, Any] = {"type": kind, "text": caption}
        if kind == "voice" and duration is not None:
            msg_content["duration"] = duration
        msg: dict[str, Any] = {
            "msgContent": msg_content,
            "fileSource": {"filePath": path},  # cryptoArgs 省略 → 自动加密
            "mentions": {},
        }

        # 发送(上传由 libsimplex 后台 worker 推进)。
        # 关键:此处**不能阻塞 actor 事件循环等送达** —— XFTP 上传 worker 也靠这同一个
        # 循环推进,await 等 sndFileCompleteXFTP 会死锁(等上传完成的循环阻塞了上传本身)。
        # 故发出后立即返回 status="sent";真实送达由 sndFileCompleteXFTP 事件异步推进,
        # 经 _snd_waiters 供需要时另查,或由接收端(rcvFileComplete)确认。
        try:
            items = await api.api_send_messages(["direct", contact_id], [msg])
        except Exception:  # noqa: BLE001
            raise
        sent_file_id = None
        try:
            sent_file_id = items[0]["chatItem"]["file"]["fileId"] if items else None
        except (KeyError, IndexError, TypeError):
            pass

        result: dict[str, Any] = {
            "contact_id": contact_id,
            "file": path,
            "kind": kind,
            "file_id": sent_file_id,
            "status": "sent",
        }
        return result

    def send_file(
        self,
        contact_id: int,
        path: str,
        caption: str = "",
        kind: str = "file",
        duration: Optional[int] = None,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        return self._submit(
            self._a_send_file(contact_id, path, caption, kind, duration, timeout),
            timeout=timeout + 30,
        )

    # ------------------------------------------------------------------ #
    # 文件接收(显式下载:bot 收到 fileInvitation 后需主动 receive_file)
    # ------------------------------------------------------------------ #
    def list_inbox_files(self, contact_id: Optional[int] = None) -> list[dict[str, Any]]:
        """列出缓冲的待下载文件邀请(不消费)。"""
        with self._inbox_lock:
            return [
                it.to_dict()
                for it in self._inbox_files
                if contact_id is None or it.contact_id == contact_id
            ]

    def _pop_inbox_file(self, file_id: int) -> None:
        with self._inbox_lock:
            self._inbox_files = [f for f in self._inbox_files if f.file_id != file_id]

    async def _a_receive_file(self, file_id: int) -> dict[str, Any]:
        """api_receive_file 接受下载(非阻塞)。下载 worker 靠 actor 循环推进,
        故**不能在此循环里 await rcvFileComplete**(同 send 的死锁)。接受后立即返回
        status="receiving";完成由 rcvFileComplete 事件异步填 _rcv_done,经
        poll_receive_file 另查。"""
        api = self._require_client().api
        Path(self._file_download_dir).mkdir(parents=True, exist_ok=True)

        client = self._require_client()

        def _fid_of(item: Any) -> Optional[int]:
            try:
                return (item.get("chatItem", {}) or {}).get("file", {}).get("fileId")
            except AttributeError:
                return None

        # 注册一次性完成/错误 watcher(不 await,事件驱动)
        self._rcv_watchers[file_id] = True

        @client.on_event("rcvFileComplete")
        async def _on_done(evt):  # noqa: ANN001
            item = evt.get("chatItem", {})
            fid = _fid_of(item)
            if fid in self._rcv_watchers:
                fpath = (item.get("chatItem", {}) or {}).get("file", {}).get("fileSource", {}).get(
                    "filePath"
                )
                # libsimplex 常返回裸文件名(相对 files_folder);拼成绝对路径便于读取
                if fpath and not Path(fpath).is_absolute():
                    fpath = str(Path(self._file_download_dir) / fpath)
                self._rcv_done[fid] = {"ok": True, "saved_path": fpath}
                self._rcv_watchers.pop(fid, None)

        @client.on_event("rcvFileError")
        async def _on_err(evt):  # noqa: ANN001
            item = evt.get("chatItem_") or evt.get("chatItem", {}) or {}
            fid = _fid_of(item)
            if fid in self._rcv_watchers:
                self._rcv_done[fid] = {"ok": False, "error": evt.get("errorMessage", "未知接收错误")}
                self._rcv_watchers.pop(fid, None)

        await api.api_receive_file(file_id)
        return {"file_id": file_id, "status": "receiving"}

    def receive_file(self, file_id: int) -> dict[str, Any]:
        return self._submit(self._a_receive_file(file_id), timeout=60)

    # ------------------------------------------------------------------ #
    # A2H 审批(功能块②:敏感操作 → 人端加密确认,approve 才动手)
    # ------------------------------------------------------------------ #
    def set_a2h_approver(self, contact_id: int, name: str) -> None:
        self._a2h_approver_cid = contact_id
        self._a2h_approver_name = name

    def _a2h_maybe_resolve(self, text: str) -> None:
        """识别 approver 的 'yes/no <request_id>' 裁决并 resolve 对应 Future。
        只认严格格式 + 精确 request_id 匹配,防把闲聊误判为裁决。"""
        parts = (text or "").strip().lower().split()
        if len(parts) != 2 or parts[0] not in ("yes", "no"):
            return
        verdict_word, rid = parts
        fut = self._a2h_waiters.get(rid)
        if fut is not None and not fut.done():
            fut.get_loop().call_soon_threadsafe(fut.set_result, verdict_word == "yes")
            self._a2h_pending.pop(rid, None)

    async def _a_a2h_request(self, request_id: str, action: str, reason: str, timeout: float) -> dict[str, Any]:
        """向 approver 发审批卡片并阻塞等裁决(fail-closed:超时/异常=拒绝)。"""
        if self._a2h_approver_cid is None:
            return {"approved": False, "error": "未设置审批人(先 simplex_a2h_set_approver)"}
        api = self._require_client().api
        cid = self._a2h_approver_cid

        card = f"[A2H {request_id}] 审批请求\n动作: {action}\n原因: {reason}\n\n回复 'yes {request_id}' 批准 / 'no {request_id}' 拒绝"
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._a2h_waiters[request_id] = fut
        self._a2h_pending[request_id] = {"action": action, "reason": reason, "ts": time.time()}

        try:
            await api.api_send_text_message(["direct", cid], card)
        except Exception as e:  # noqa: BLE001
            self._a2h_waiters.pop(request_id, None)
            self._a2h_pending.pop(request_id, None)
            return {"approved": False, "error": f"审批卡片发送失败:{e!r}"}

        t0 = time.time()
        try:
            approved = await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
            return {
                "approved": bool(approved),
                "by": self._a2h_approver_name,
                "latency_s": round(time.time() - t0, 1),
                "request_id": request_id,
            }
        except asyncio.TimeoutError:
            self._a2h_waiters.pop(request_id, None)
            self._a2h_pending.pop(request_id, None)
            return {
                "approved": False,
                "timeout": True,
                "request_id": request_id,
                "error": f"{timeout}s 内审批人未回复(fail-closed 按拒绝)",
            }

    def a2h_request(self, request_id: str, action: str, reason: str, timeout: float = 300.0) -> dict[str, Any]:
        return self._submit(self._a_a2h_request(request_id, action, reason, timeout), timeout=timeout + 30)

    def a2h_pending(self) -> list[dict[str, Any]]:
        """列出未裁决的审批请求。"""
        now = time.time()
        return [
            {"request_id": rid, "action": p["action"], "reason": p["reason"], "age_s": round(now - p["ts"], 1)}
            for rid, p in self._a2h_pending.items()
        ]

    # ------------------------------------------------------------------ #
    # 会话轮换(功能块③:换假名切断纵向可链接性)
    # ------------------------------------------------------------------ #
    async def _a_update_display_name(self, new_name: str) -> dict[str, Any]:
        """改当前 active user 的显示名(rename 轮换;同连接内换假名,消息流不中断)。"""
        api = self._require_client().api
        user = await api.api_get_active_user()
        if not user:
            raise RuntimeError("无 active user")
        prof = user.get("profile", {})
        new_profile = {
            "displayName": new_name,
            "fullName": prof.get("fullName", ""),
        }
        if prof.get("shortDescr"):
            new_profile["shortDescr"] = prof["shortDescr"]
        if prof.get("image"):
            new_profile["image"] = prof["image"]
        summary = await api.api_update_profile(user["userId"], new_profile)
        self._display_name = new_name
        return {"old_name": prof.get("displayName"), "new_name": new_name, "updated": summary is not None}

    def update_display_name(self, new_name: str) -> dict[str, Any]:
        return self._submit(self._a_update_display_name(new_name))

    def poll_receive_file(self, file_id: int) -> dict[str, Any]:
        """查询某文件下载是否完成(由 rcvFileComplete 事件异步填充)。"""
        if file_id in self._rcv_done:
            res = self._rcv_done.pop(file_id)
            if res.get("ok"):
                self._pop_inbox_file(file_id)
            return {"file_id": file_id, "done": True, **res}
        return {"file_id": file_id, "done": False, "status": "downloading" if file_id in self._rcv_watchers else "unknown"}



    def read_inbox(self, limit: int = 20, contact_id: Optional[int] = None) -> list[dict[str, Any]]:
        """非破坏性读取收件箱缓冲(不弹出,便于重复诊断)。"""
        with self._inbox_lock:
            items = [
                it
                for it in self._inbox
                if contact_id is None or it.contact_id == contact_id
            ]
            return [it.to_dict() for it in items[-limit:]]

    def drain_inbox(self, contact_id: Optional[int] = None) -> list[dict[str, Any]]:
        """弹出式读取(消费收件箱)。"""
        with self._inbox_lock:
            if contact_id is None:
                out = [it.to_dict() for it in self._inbox]
                self._inbox.clear()
                return out
            keep, take = [], []
            for it in self._inbox:
                (take if it.contact_id == contact_id else keep).append(it.to_dict())
            self._inbox = keep
            return take

    # ------------------------------------------------------------------ #
    # 联系人解析(display name 或 id)
    # ------------------------------------------------------------------ #
    def resolve_contact(self, ref: str | int) -> Optional[dict[str, Any]]:
        """把 display name / id 解析为联系人 dict;找不到返回 None。"""
        contacts = self.list_contacts()
        if isinstance(ref, int) or (isinstance(ref, str) and ref.isdigit()):
            cid = int(ref)
            for c in contacts:
                if c["contact_id"] == cid:
                    return c
            return None
        ref_l = str(ref).strip().lower()
        for c in contacts:
            if (c.get("display_name") or "").lower() == ref_l:
                return c
        # 子串模糊(便于 agent 说 "给 X" 时部分匹配)
        for c in contacts:
            if ref_l and ref_l in (c.get("display_name") or "").lower():
                return c
        return None

    # ------------------------------------------------------------------ #
    # 关闭
    # ------------------------------------------------------------------ #
    def shutdown(self) -> None:
        if self._loop and self._thread and self._thread.is_alive():
            try:
                self._submit(self._a_shutdown(), timeout=20)
            except Exception:  # noqa: BLE001
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10)

    async def _a_shutdown(self) -> None:
        if self._serve_task:
            self._serve_task.cancel()
        if self._client is not None:
            await self._client.__aexit__()
            self._client = None
