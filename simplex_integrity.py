"""simplex_integrity.py — Agent-First OS L2 功能块:文件完整性校验三件套

用户的关注点:双方都用我们的 IM 时,端到端文件传输加哈希校验,保证双方文档一致;
并覆盖"安装包/公共镜像下载"的跨通道校验场景。

先厘清 XFTP 已有的保障(避免重复造轮子,protocol/xftp.md 确认):
  - 文件 E2E 加密(NaCl secret_box),中继只见密文;
  - 发送方算文件 SHA512,接收方下载后**已对 SHA512 校验**,失败即中止;
  - 每个 chunk 也带 SHA512,上传即验;
  - digest+密钥经 E2E 加密的 SMP 通道带外传递。
  ⇒ "传输不被篡改"已被协议强制。本块补的是 XFTP **没有**的三层:

  ① 签名清单(身份+出处)—— XFTP 保证"完整到达",不保证"是你以为的那个人发的"。
     发送方对文件 SHA256 出一份签名清单(带发送者身份),接收方据此确认"来自 X 的这份文件"。
     信任根:自研 IM 双方都是我们的 agent,信任根来自带外/带内通道(见 §信任根)。
  ② 通用哈希校验 —— 独立工具:任意文件算 SHA256 / 与给定哈希或文件对比。
     用于安装包、公共镜像下载的跨通道一致性(哈希经我们的 IM 发布,用户拿它验镜像副本)。
  ③ 已有校验可视化 —— 把 XFTP 内置 SHA512 校验结果显式呈现(它本来就有,只是不可见),
     作为"已验证一致"的直观反馈。

信任根(诚实声明):
  本块 v1 用 **HMAC-SHA256 + 共享信任根**(trust root)。双方都是我们的 agent,信任根经
  带内 E2E 通道首次交换 / 带外预置。这不是抗"中继作恶"的完整公钥方案——中继是我们的
  VPS,且 HMAC 密钥不过中继(经 E2E 通道交换),故 HMAC 已够防"传输中被替换文件"。
  后续可升级为 per-identity Ed25519 签名(libsimplex 不暴露连接私钥,需另建身份密钥对),
  见 §后续。文件清单格式预留 algorithm 字段便于升级。

契约:每个工具返回 {ok, output|error, diagnosable}(架构 §3.2);副作用(send)过 policy。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
import simplex_tools as _st  # noqa: E402  (send_message / read_messages / resolve_contact)
import simplex_files as _sf  # noqa: E402  (send_file / receive_file)
from simplex_runtime import SimplexRuntime  # noqa: E402

try:
    from policy_engine import policy_check_daemon  # noqa: E402
except Exception:  # noqa: BLE001
    def policy_check_daemon(tool: str, args: dict):  # type: ignore
        return ("allow", "policy_engine 不可用,standalone 放行")

# 信任根存储(per-install;双方 agent 各自持有,经 E2E 通道交换后对齐)
_TRUST_FILE = Path.home() / ".local" / "share" / "aureon" / "simplex" / "integrity_trust.json"
_MANIFEST_PREFIX = "[SIGMANIFEST]"
_MANIFEST_VERSION = 2  # v2 = per-identity Ed25519;v1 = per-contact HMAC(向后兼容保留)


def _ok(output: Any, **extra) -> dict[str, Any]:
    return {"ok": True, "output": output, **extra}


def _err(reason: str, diagnosable: str, **extra) -> dict[str, Any]:
    return {"ok": False, "error": reason, "diagnosable": diagnosable, **extra}


def _runtime() -> SimplexRuntime:
    return SimplexRuntime.instance()


# ────────────────────────────────────────────────────────────────────── #
# 身份密钥对(per-identity Ed25519,按 _db_prefix 隔离)
# ────────────────────────────────────────────────────────────────────── #

def _resolve_db_prefix(rt: SimplexRuntime, *, allow_fallback: bool = True) -> str:
    """统一解析本实例的 db_prefix(身份/信任/下载目录共用的隔离根)。

    铁律(红线 1):身份来源只用 `rt._db_prefix`(getattr 兜底 env),绝不靠
    DM_IDENTITY/SECUREDM_INSTANCE env 猜 —— bob 是 argv 覆写模块全局、不写 env,
    读 env 会回退 oiagent 导致 oiagent/bob 共用一把身份钥(这错误我们已犯过一次)。

    `allow_fallback=True`(仅下载目录等**非密钥**场景)才允许回退到共享默认目录;
    身份密钥/对方公钥这类**绝不能串**的场景必须 `allow_fallback=False`:
    一旦 `_db_prefix` 与 env 都给不出一个**实例专属** prefix,立刻 raise,
    绝不静默落到共享默认目录 —— 那正是 oiagent/bob 串钥(「⚠ 公钥变更」)的根。
    """
    prefix = (getattr(rt, "_db_prefix", "") or "").strip()
    if prefix:
        return prefix
    env_prefix = (os.environ.get("DM_DB_PREFIX", "") or "").strip()
    if env_prefix:
        return env_prefix
    if not allow_fallback:
        raise RuntimeError(
            "db_prefix 为空:无法为身份密钥确定实例专属隔离目录,"
            "拒绝回退到共享默认目录(防 oiagent/bob 串钥)。"
            "请确保 setup 传入非空 db_prefix(不要用空字符串 argv)。"
        )
    return str(
        Path.home() / ".local" / "share" / "aureon" / "simplex"
        / f"{os.environ.get('SECUREDM_INSTANCE') or os.environ.get('DM_IDENTITY', 'oiagent')}_simplex"
    )


def _identity_key_paths(rt: SimplexRuntime) -> tuple[Path, Path]:
    """身份私钥/公钥文件路径,按 `_db_prefix` 隔离(同 home 多实例不串钥)。

    走 `_resolve_db_prefix(allow_fallback=False)`:db_prefix 缺失时**拒绝**,
    不回退共享默认目录(防串钥)。与 _candidate_download_dirs 同一套纪律,
    但下载目录允许 fallback(找不到文件只是诊断性问题,不涉密钥)。
    """
    parent = Path(_resolve_db_prefix(rt, allow_fallback=False)).parent
    return parent / "identity_ed25519.key", parent / "identity_ed25519.pub"


def _identity_owner_path(rt: SimplexRuntime) -> Path:
    """身份密钥的属主标记文件(记录这把钥是为哪个 db_prefix 生成的)。"""
    key_path, _ = _identity_key_paths(rt)
    return key_path.parent / "identity_ed25519.owner"


def _load_or_create_identity(rt: SimplexRuntime) -> Ed25519PrivateKey:
    """读已有身份私钥;没有则生成一对并落盘(私钥 0600,公钥同目录)。幂等不覆盖。

    私钥永不离开本机、永不进 manifest/消息/log(红线 2);manifest 只放公钥。

    防串钥第二道闸:首次生成时把属主 db_prefix 写进 `identity_ed25519.owner`;
    之后每次加载都核对 —— 若现有密钥的属主与当前 `_db_prefix` 不符,说明这把钥
    物理上是别的实例留下的(同目录串钥),**拒绝复用**并显式报错,而不是悄悄拿去签。
    (无 owner 文件的旧密钥视为历史遗留,按当前 prefix 补登记,兼容既有安装。)
    """
    key_path, pub_path = _identity_key_paths(rt)
    prefix = _resolve_db_prefix(rt, allow_fallback=False)
    if key_path.exists():
        owner_path = _identity_owner_path(rt)
        if owner_path.exists():
            try:
                owner = owner_path.read_text(encoding="utf-8").strip()
            except Exception:  # noqa: BLE001
                owner = ""
            if owner and owner != prefix:
                raise RuntimeError(
                    f"身份密钥属主不符:目录里的密钥是为 '{owner}' 生成的,"
                    f"当前实例 db_prefix 是 '{prefix}'。拒绝复用他实例密钥(防串钥)。"
                    "若确认要换绑,请备份后删除该目录 identity_ed25519.* 再重试。"
                )
        else:
            # 旧密钥补登记属主(幂等;best-effort,不阻断)
            try:
                owner_path.write_text(prefix, encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        return Ed25519PrivateKey.from_private_bytes(key_path.read_bytes())
    priv = Ed25519PrivateKey.generate()
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(priv.private_bytes_raw())
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    pub_path.write_bytes(priv.public_key().public_bytes_raw())
    try:
        _identity_owner_path(rt).write_text(prefix, encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return priv


def _identity_pubkey_b64(rt: SimplexRuntime) -> str:
    """本端身份公钥(base64, raw 32 字节)。"""
    return base64.b64encode(_load_or_create_identity(rt).public_key().public_bytes_raw()).decode("ascii")


def _identity_fingerprint(rt: SimplexRuntime) -> str:
    """身份公钥指纹(sha256 前 16 hex),供 UI 带外核对显示。"""
    return hashlib.sha256(base64.b64decode(_identity_pubkey_b64(rt))).hexdigest()[:16]


# ────────────────────────────────────────────────────────────────────── #
# 对方公钥存储(per-contact Ed25519 公钥,TOFU 防降级)
# ────────────────────────────────────────────────────────────────────── #

def _pubkeys_file(rt: SimplexRuntime) -> Path:
    """对方公钥存储文件,按 `_db_prefix` 隔离(同身份钥同目录;缺失同样拒绝,防串)。"""
    return Path(_resolve_db_prefix(rt, allow_fallback=False)).parent / "integrity_pubkeys.json"


def _load_pubkeys(rt: SimplexRuntime) -> dict[str, Any]:
    try:
        f = _pubkeys_file(rt)
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {"contacts": {}}


def _save_pubkeys(rt: SimplexRuntime, data: dict[str, Any]) -> None:
    f = _pubkeys_file(rt)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(f, 0o600)
    except OSError:
        pass


def _pubkey_for(rt: SimplexRuntime, contact_id: int) -> str | None:
    """该联系人已固定的对方公钥(b64);未建立返回 None。"""
    return _load_pubkeys(rt)["contacts"].get(str(contact_id), {}).get("pubkey")


def _set_pubkey(rt: SimplexRuntime, contact_id: int, pubkey_b64: str,
                display: str = "", fp: str = "", allow_overwrite: bool = False) -> dict[str, Any]:
    """固定某联系人的对方公钥。TOFU 防降级(红线 3):已固定且新钥不同 → 拒绝覆盖并告警。

    返回 {"ok":bool, "alert":str|None, "changed":bool}。同公钥重入=幂等 ok。
    """
    data = _load_pubkeys(rt)
    cid = str(contact_id)
    existing = data["contacts"].get(cid, {}).get("pubkey")
    if existing and existing != pubkey_b64 and not allow_overwrite:
        alert = (f"⚠ 公钥变更告警:{display or cid} 的公钥与已固定值不一致,可能是中间人换绑攻击。"
                 "已拒绝覆盖(保留旧公钥)。若确为对方更换设备,请带外核实后再重新建立信任。")
        return {"ok": False, "alert": alert, "changed": False}
    data["contacts"][cid] = {
        "pubkey": pubkey_b64,
        "fp": fp or hashlib.sha256(base64.b64decode(pubkey_b64)).hexdigest()[:16],
        "display": display,
        "established": time.time(),
    }
    _save_pubkeys(rt, data)
    return {"ok": True, "alert": None, "changed": existing != pubkey_b64}


def _consume_trust_message(rt: SimplexRuntime, contact_id: int, text: str) -> dict[str, Any] | None:
    """解析一条 `[SIGMANIFEST]trust {json}` 消息并消费(固定对方公钥)。非 trust 返回 None。

    这就是根治"发了没人收"的消费端:trust 公钥随 E2E 文本到达,验证侧轮询历史时顺带消费。
    """
    prefix = f"{_MANIFEST_PREFIX}trust "
    if not text.startswith(prefix):
        return None
    try:
        obj = json.loads(text[len(prefix):])
    except Exception:  # noqa: BLE001
        return {"ok": False, "alert": None, "error": "trust 消息 JSON 解析失败"}
    pub = obj.get("pubkey", "")
    if obj.get("algorithm") != "Ed25519" or not pub:
        return {"ok": False, "alert": None, "error": "trust 消息缺 Ed25519 公钥"}
    try:
        base64.b64decode(pub)
    except Exception:  # noqa: BLE001
        return {"ok": False, "alert": None, "error": "trust 公钥 base64 非法"}
    return _set_pubkey(rt, contact_id, pub, obj.get("identity", ""), obj.get("fp", ""))


# ────────────────────────────────────────────────────────────────────── #
# 哈希基础
# ────────────────────────────────────────────────────────────────────── #

def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ────────────────────────────────────────────────────────────────────── #
# 信任根管理(HMAC 共享密钥,per-contact)
# ────────────────────────────────────────────────────────────────────── #

def _load_trust() -> dict[str, Any]:
    try:
        if _TRUST_FILE.exists():
            return json.loads(_TRUST_FILE.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        pass
    return {"contacts": {}}


def _save_trust(data: dict[str, Any]) -> None:
    _TRUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    _TRUST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(_TRUST_FILE, 0o600)
    except OSError:
        pass


def _trust_key_for(contact_id: int) -> str | None:
    return _load_trust()["contacts"].get(str(contact_id), {}).get("key")


def _set_trust_key(contact_id: int, key: str, display: str = "") -> None:
    data = _load_trust()
    data["contacts"][str(contact_id)] = {"key": key, "display": display, "established": time.time()}
    _save_trust(data)


def simplex_trust_import(contact: str, key: str) -> dict[str, Any]:
    """导入对方经带外渠道提供的信任根密钥(对齐双方签名验证)。

    与 simplex_trust_establish(生成新密钥并经 E2E 通道发出)互补:当对方已经建好
    密钥并经带外(安全渠道)给你时,用此导入对齐 —— 这样对方用 send_file_signed 发的
    文件,你才能用同一密钥验 HMAC。真实部署中带外预置;测试/演示时经共享文件传递。
    """
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 '{contact}'", "用 simplex_list_contacts 查看。")
    key = key.strip()
    if len(key) != 64 or not all(c in "0123456789abcdef" for c in key.lower()):
        return _err("密钥格式无效", "期望 64 位十六进制(32 字节)。")
    _set_trust_key(resolved["contact_id"], key, resolved.get("display_name", ""))
    return _ok({"contact_id": resolved["contact_id"]},
               diagnosable=f"已导入 {resolved.get('display_name')} 的信任根,可验证其签名文件。")


def _sign(key_hex: str, payload: str) -> str:
    return hmac.new(bytes.fromhex(key_hex), payload.encode("utf-8"), hashlib.sha256).hexdigest()


# ────────────────────────────────────────────────────────────────────── #
# ① 签名清单(身份+出处)
# ────────────────────────────────────────────────────────────────────── #

def _current_sender(rt) -> str:
    """本端对外显示身份(manifest sender / trust identity 用的"来自 X")。

    必须取 **runtime 当前显示名 rt._display_name**(用户「改 ID」/api_set_identity 实时更新它),
    不能取 rt.status().active_user —— 那是 SimpleX 侧 active_user profile 的 displayName,
    只在 setup 时按 argv 身份写入一次,之后用户改名不更新,会停在启动身份(窗口1/窗口2)。
    修"已验证来自 窗口1"应为窗口2 + 右上角不同步的根因。
    """
    name = (getattr(rt, "_display_name", "") or "").strip()
    return name or "agent"


def simplex_trust_establish(contact: str) -> dict[str, Any]:
    """与联系人建立文件签名信任根:把**本端身份公钥**经 E2E 通道发给对方(TOFU)。

    v2 起改为 per-identity Ed25519:确保本端身份密钥已建,发自己的公钥(不含私钥,红线 2)。
    对方在验证文件时轮询聊天历史被动消费此 trust 消息、固定我方公钥(见 §2.B 被动消费);
    本端发送后也把"我方公钥"记入对方 cid 的信任存储自证(本端验自己发的文件时可用)。
    公钥不过中继明文(经 E2E 通道);旧 HMAC 路径保留向后兼容(见 verify 分流)。
    """
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 '{contact}'", "用 simplex_list_contacts 查看现有联系人。")
    cid = resolved["contact_id"]
    priv = _load_or_create_identity(rt)
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    fp = _identity_fingerprint(rt)
    sender = _current_sender(rt)
    # 经 E2E 通道把**公钥**发给对方(JSON,不再是裸 hex HMAC key)
    trust_obj = {"v": 1, "algorithm": "Ed25519", "pubkey": pub_b64, "fp": fp, "identity": sender}
    msg = f"{_MANIFEST_PREFIX}trust {json.dumps(trust_obj, ensure_ascii=False)}"
    r = _st.call_tool("simplex_send_message", {"contact": str(cid), "text": msg})
    if not r["ok"]:
        return _err("信任根交换发送失败", r.get("diagnosable", ""))
    # 本端自证:把我方公钥记入对方 cid 存储(本端 verify 自己发的文件时可对齐)
    _set_pubkey(rt, cid, pub_b64, resolved.get("display_name", ""), fp)
    return _ok(
        {"contact_id": cid, "key_established": True, "algorithm": "Ed25519", "fp": fp},
        diagnosable=f"已与 {resolved.get('display_name')} 建立文件签名信任(Ed25519 公钥经 E2E 发出,指纹 {fp})。",
    )


def simplex_send_file_signed(contact: str, path: str, caption: str = "") -> dict[str, Any]:
    """发送文件 + 签名清单(SHA256 + Ed25519),接收方据此确认"来自 X 的这份文件"。

    v2 起不再依赖 per-contact HMAC key:用本端 per-identity Ed25519 私钥签名。
    若该联系人还没有"我方公钥已发出"记录,自动先发一次 trust 公钥(幂等),
    保证对方验证时已能从我方公钥验签(根治"发了没人收/验不了")。
    """
    verdict, reason = policy_check_daemon("simplex_send_file_signed", {"contact": contact, "path": path})
    if verdict == "deny":
        return _err("审批拒绝", f"policy deny:{reason}")
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 '{contact}'", "用 simplex_list_contacts 查看。")
    cid = resolved["contact_id"]
    p = Path(path).expanduser()
    if not p.is_file():
        return _err(f"文件不存在: {p}", "确认路径正确且 daemon 进程可读。")

    priv = _load_or_create_identity(rt)
    pub_b64 = base64.b64encode(priv.public_key().public_bytes_raw()).decode("ascii")
    # 确保对方已收到我方公钥(没有记录则自动补发一次 trust;幂等)
    if not _pubkey_for(rt, cid):
        simplex_trust_establish(contact)

    digest = _sha256_file(p)
    size = p.stat().st_size
    # 清单 payload(被签内容):file 名 + sha256 + size + sender 身份(格式保持 v1 不变,便于旧接收方理解结构)
    sender = _current_sender(rt)
    payload = f"{p.name}|{digest}|{size}|{sender}"
    sig = base64.b64encode(priv.sign(payload.encode("utf-8"))).decode("ascii")
    manifest = {
        "v": _MANIFEST_VERSION,
        "algorithm": "Ed25519",
        "pubkey": pub_b64,
        "file": p.name,
        "sha256": digest,
        "size": size,
        "sender": sender,
        "sig": sig,
        "ts": int(time.time()),
    }
    # 先发文件本体,再发签名清单(文本)
    fr = _sf.call_tool("simplex_send_file", {"contact": str(cid), "path": str(p), "caption": caption})
    if not fr["ok"]:
        return _err("文件本体发送失败", fr.get("diagnosable", ""))
    mtext = f"{_MANIFEST_PREFIX}file {json.dumps(manifest, ensure_ascii=False)}"
    mr = _st.call_tool("simplex_send_message", {"contact": str(cid), "text": mtext})
    if not mr["ok"]:
        return _err("签名清单发送失败(文件本体已发出)", mr.get("diagnosable", ""))
    return _ok(
        {"file": p.name, "sha256": digest, "size": size, "sender": sender, "signed": True,
         "algorithm": "Ed25519", "fp": _identity_fingerprint(rt)},
        diagnosable=f"已发送 {p.name} 并附 Ed25519 签名清单;接收方可用 simplex_verify_received_file 验证出处。",
    )


def simplex_verify_received_file(contact: str, path: str, timeout: float = 20.0) -> dict[str, Any]:
    """验证一个已接收文件是否带有效签名清单(出处+一致性)。

    从该联系人最近消息里找该文件的签名清单,重算本地文件 SHA256 比对 + 按算法验签。
    清单是签名发送方随文件发出的一条文本消息,可能晚于文件到达 —— 故轮询等待 timeout 秒。

    v2:轮询聊天历史时**顺带消费其中的 trust 公钥消息**(§2.B 被动消费,根治"发了没人收"),
    把对方 Ed25519 公钥固定进信任存储。验签按 manifest.algorithm 分流:
      - "Ed25519":用该 cid 固定的对方公钥验签;并核对 manifest 自清 pubkey 与固定公钥一致
        (不一致=疑似换绑攻击,signature_valid=False 且显式 alert)。
      - "HMAC-SHA256" 或缺省:走旧 per-contact HMAC 路径(向后兼容,红线 4)。
    """
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    resolved = rt.resolve_contact(contact)
    if resolved is None:
        return _err(f"没有联系人 '{contact}'", "用 simplex_list_contacts 查看。")
    cid = resolved["contact_id"]
    p = Path(path).expanduser()
    if not p.is_file():
        return _err(f"文件不存在: {p}", "确认已用 simplex_receive_file 下载完成。")

    # 轮询等清单(清单文本消息可能晚于文件本体到达)。从持久聊天历史读(跨进程可见,
    # 不依赖内存收件箱 —— 收件箱重启即空,签名验证必须能回溯)。
    # 匹配策略:本地文件可能被重命名(payload.bin → payload_1.bin),故先按 sha256 内容
    # 匹配(最强),找不到再按文件名匹配。
    local_hash = _sha256_file(p)
    deadline = time.time() + timeout
    manifest = None
    trust_alert: str | None = None
    while time.time() < deadline and manifest is None:
        # 关键:必须用 chat_items(原始 `/_get chat @cid count=N`,每会话完整消息列表),
        # 不能走 chat_texts(api_get_chats 是"会话列表"查询,每会话只带最新一条)——
        # 否则同会话的 trust 公钥消息(早于 file manifest)会被漏掉,被动消费永远拿不到公钥。
        try:
            items = rt.chat_items(cid, limit=60)
        except Exception:  # noqa: BLE001
            items = []
        # 只取对方(dir=="them")的消息:本窗口自己发出的 trust 公告(dir=="me")也会在
        # 同一对话里,若一并消费,会把"自己的身份钥"错钉成"对方公钥"——这正是
        # "两窗口交换发文件都来自 bob"的根因(oiagent 把 bob 的钥钉成了对方公钥)。
        # manifest 同样只认对方发的,自己的回声不算。
        texts = [it.get("text", "") for it in items if it.get("dir") == "them"]
        # 先扫描本轮 trust 公告(不立即消费),拿到"对方经 E2E 公告的公钥"集合。
        # 用于区分合法换钥(对方换设备/重新生成身份钥,经 E2E 通道公告新钥)
        # 与中间人换绑(manifest 塞陌生钥、无对应 trust 公告)—— 攻击者无法伪造该联系人的 trust。
        announced: list[dict[str, Any]] = []
        for t in texts:
            prefix = f"{_MANIFEST_PREFIX}trust "
            if not t.startswith(prefix):
                continue
            try:
                obj = json.loads(t[len(prefix):])
            except Exception:  # noqa: BLE001
                continue
            if obj.get("algorithm") == "Ed25519" and obj.get("pubkey"):
                announced.append(obj)
        # 被动消费 trust 公钥(默认仍 TOFU 拒绝覆盖);告警只留最近一条
        for t in texts:
            c = _consume_trust_message(rt, cid, t)
            if c and c.get("alert"):
                trust_alert = c["alert"]
        by_name = None
        for t in texts:
            if not t.startswith(f"{_MANIFEST_PREFIX}file "):
                continue
            try:
                mm = json.loads(t[len(f"{_MANIFEST_PREFIX}file "):])
            except Exception:  # noqa: BLE001
                continue
            # 合法换钥识别:manifest 自清钥 ≠ 已固定钥,但本轮 trust 公告里有一把 == manifest 钥
            # → 对方经 E2E 正式公告了换钥(合法轮换,非中间人),允许换绑后按新钥验签。
            m_pub = mm.get("pubkey", "")
            pinned = _pubkey_for(rt, cid)
            if m_pub and pinned and m_pub != pinned:
                if any(a.get("pubkey") == m_pub for a in announced):
                    _set_pubkey(rt, cid, m_pub,
                                next((a.get("identity", "") for a in announced if a.get("pubkey") == m_pub), ""),
                                allow_overwrite=True)
                    trust_alert = ("ℹ 已接受对方经 E2E 公告的新公钥(合法换钥/设备轮换),"
                                   "按新公钥验签。若非你预期的对方操作,请带外核实。")
            if mm.get("sha256") == local_hash:      # 内容匹配(优先)
                manifest = mm
                break
            if mm.get("file") == p.name:            # 文件名匹配(兜底)
                by_name = mm
        if manifest is None and by_name is not None:
            manifest = by_name
        if manifest is None:
            time.sleep(1.5)
    if manifest is None:
        return _err(
            f"未找到该文件的签名清单(等了 {timeout}s,本地 sha256={local_hash[:12]}…)",
            "该文件可能未经签名发送(用 simplex_send_file 而非 simplex_send_file_signed),或清单消息尚未到达。",
        )

    local = _sha256_file(p)
    payload = f"{manifest['file']}|{manifest['sha256']}|{manifest['size']}|{manifest['sender']}"
    hash_ok = (local == manifest.get("sha256"))
    algorithm = manifest.get("algorithm", "HMAC-SHA256")
    sig_ok = False
    alert: str | None = trust_alert

    if algorithm == "Ed25519":
        pub_b64 = _pubkey_for(rt, cid)
        if not pub_b64:
            return _err(
                "未建立对方公钥",
                f"还没有 {contact} 的 Ed25519 公钥。请先 simplex_trust_establish('{contact}') 互换公钥,或等对方 trust 公钥到达后再验。",
                output={"file": p.name, "hash_match": hash_ok, "signature_valid": False,
                        "algorithm": algorithm, "verified": False},
            )
        # 核对 manifest 自清 pubkey 与固定公钥一致(防换绑:清单里塞了别的公钥)
        m_pub = manifest.get("pubkey", "")
        if m_pub and m_pub != pub_b64:
            alert = ("⚠ 公钥变更告警:签名清单携带的公钥与该联系人已固定公钥不一致,"
                     "疑似中间人换绑攻击。signature_valid=False,请勿使用此文件。")
            sig_ok = False
        else:
            try:
                Ed25519PublicKey.from_public_bytes(base64.b64decode(pub_b64)).verify(
                    base64.b64decode(manifest.get("sig", "")), payload.encode("utf-8"))
                sig_ok = True
            except Exception:  # noqa: BLE001
                sig_ok = False
    else:  # HMAC-SHA256 或缺省:旧路径向后兼容(红线 4)
        key = _trust_key_for(cid)
        if not key:
            return _err("无该联系人信任根", f"先 simplex_trust_establish('{contact}')。")
        expect_sig = _sign(key, payload)
        sig_ok = hmac.compare_digest(expect_sig, manifest.get("sig", ""))

    verified = hash_ok and sig_ok
    out = {
        "file": p.name,
        "hash_match": hash_ok,
        "signature_valid": sig_ok,
        "sender": manifest.get("sender"),
        "claimed_sha256": manifest.get("sha256"),
        "local_sha256": local,
        "algorithm": algorithm,
        "verified": verified,
    }
    if alert:
        out["alert"] = alert
    diag = (
        f"✓ 出处与一致性均验证通过({algorithm}):来自 {manifest.get('sender')},SHA256 一致。"
        if verified
        else ("✗ 验证失败:" + ("" if hash_ok else " 文件哈希与清单不一致(可能被替换);")
              + ("" if sig_ok else " 签名无效(出处不可信);") + " 请勿使用此文件。"
              + (f" {alert}" if alert else ""))
    )
    return _ok(out, diagnosable=diag)


def _candidate_download_dirs(rt: SimplexRuntime) -> list[Path]:
    """收集"已下载文件可能在的目录"候选列表(默认目录 + 自定义目录,去重保序)。

    背景:SecureDM 的 api_receive_file 允许用户设自定义下载目录(存于 download_dir.txt),
    下载成功后会 shutil.copy2 一份过去。verify 若只看默认目录就会"明明下载了却找不到"。
    本 helper 复刻 securedm_web._download_dir_path() 的路径规则(此处不能 import
    securedm_web,避免循环依赖):download_dir.txt = Path(db_prefix).parent / "download_dir.txt"。
    db_prefix 直接取本进程 runtime 的 `_db_prefix`(同进程单例,setup 时已按实例身份/CLI argv
    正确设置)——**不靠 env 猜 identity**:bob 这类实例的 DM_IDENTITY/DM_DB_PREFIX 是
    securedm_web.__main__ 用 argv 覆写的模块全局、不写进 env,读 env 会回退 "oiagent" 而找错。
    `_db_prefix` 缺失时回退 env DM_DB_PREFIX 的同规则解析作兜底。
    未设自定义目录时仅返回默认目录,行为与原单目录逻辑一致(回归安全)。
    """
    dirs: list[Path] = []
    default = Path(rt._file_download_dir)
    dirs.append(default)
    db_prefix = getattr(rt, "_db_prefix", "") or os.environ.get("DM_DB_PREFIX", "") or str(
        Path.home() / ".local" / "share" / "aureon" / "simplex"
        / f"{os.environ.get('SECUREDM_INSTANCE') or os.environ.get('DM_IDENTITY', 'oiagent')}_simplex"
    )
    txt = Path(db_prefix).parent / "download_dir.txt"
    try:
        custom = txt.read_text(encoding="utf-8").strip() if txt.exists() else ""
    except Exception:  # noqa: BLE001
        custom = ""
    if custom:
        c = Path(custom).expanduser()
        if c != default:
            dirs.append(c)
    return dirs


def simplex_verify_file_by_manifest(contact: str, file_name: str, timeout: float = 15.0) -> dict[str, Any]:
    """按文件名验证最近收到的文件是否带有效签名清单(供 IM 界面给收到的文件打"已验证"标)。

    与 simplex_verify_received_file 的差别:不需要本地路径 —— 在下载目录里按文件名找
    最新文件,再走同一套清单比对 + HMAC 出处校验。供 SecureDM 渲染文件消息时调用。

    下载目录候选 = 默认目录 + 自定义目录(若用户经 SecureDM 设过):api_receive_file 会
    把文件复制到自定义目录,只看默认目录会漏掉,见 _candidate_download_dirs。
    """
    rt = _runtime()
    if not (rt._thread and rt._thread.is_alive()):
        return _err("SimpleX runtime 未启动", "先调 simplex_setup。")
    dirs = _candidate_download_dirs(rt)
    # 按文件名前缀在所有候选目录找文件(payload.bin / payload_1.bin ...),合并后按 mtime 取最新
    stem, suffix = Path(file_name).stem, Path(file_name).suffix
    cands: list[Path] = []
    for d in dirs:
        if d.is_dir():
            cands.extend(c for c in d.glob(f"{stem}*{suffix}") if c.is_file())
    cands.sort(key=lambda c: c.stat().st_mtime)
    if not cands:
        searched = "; ".join(str(d) for d in dirs)
        return _err(
            f"下载目录未找到 {file_name}",
            f"在以下目录都没找到匹配文件: {searched}。确认已用 simplex_receive_file 下载完成。",
        )
    latest = cands[-1]
    # 复用主验证逻辑(按内容 sha256 匹配清单,与文件名无关)
    return simplex_verify_received_file(contact, str(latest), timeout=timeout)


# ────────────────────────────────────────────────────────────────────── #
# ② 通用哈希校验(跨通道 / 安装包 / 镜像)
# ────────────────────────────────────────────────────────────────────── #

def hash_file(path: str, algorithm: str = "sha256") -> dict[str, Any]:
    """计算任意文件的哈希(默认 SHA256)。用于发布到 IM 供他人验镜像/安装包。"""
    p = Path(path).expanduser()
    if not p.is_file():
        return _err(f"文件不存在: {p}", "确认路径正确且进程可读。")
    algorithm = algorithm.lower()
    if algorithm not in ("sha256", "sha512", "sha1", "md5"):
        return _err(f"不支持的算法: {algorithm}", "支持 sha256/sha512/sha1/md5(推荐 sha256)。")
    h = hashlib.new(algorithm)
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return _ok(
        {"file": p.name, "algorithm": algorithm, "hash": h.hexdigest(), "size": p.stat().st_size},
        diagnosable=f"{p.name} 的 {algorithm} = {h.hexdigest()}。可经 IM 发布此值供他人验证镜像副本。",
    )


def hash_verify(path: str, expect_hash: str = "", compare_file: str = "",
                algorithm: str = "sha256") -> dict[str, Any]:
    """校验文件:与给定哈希比对,或与另一文件比对(一致性)。"""
    p = Path(path).expanduser()
    if not p.is_file():
        return _err(f"文件不存在: {p}", "确认路径正确。")
    hr = hash_file(str(p), algorithm)
    if not hr["ok"]:
        return hr
    local = hr["output"]["hash"]

    if expect_hash:
        match = hmac.compare_digest(local.lower(), expect_hash.strip().lower())
        return _ok(
            {"file": p.name, "local": local, "expect": expect_hash.strip(), "match": match},
            diagnosable=(
                f"✓ {p.name} 哈希与期望值一致。" if match
                else f"✗ {p.name} 哈希与期望值**不一致**!本地 {local} ≠ 期望 {expect_hash.strip()}。文件可能被篡改/下载损坏,请勿使用。"
            ),
        )
    if compare_file:
        q = Path(compare_file).expanduser()
        if not q.is_file():
            return _err(f"对比文件不存在: {q}", "确认对比文件路径。")
        other = hash_file(str(q), algorithm)["output"]["hash"]
        match = (local == other)
        return _ok(
            {"file": p.name, "compare_file": q.name, "match": match, "sha256": local},
            diagnosable=(
                f"✓ 两文件 {algorithm} 一致(内容相同)。" if match
                else f"✗ 两文件 {algorithm} 不一致:{p.name} 与 {q.name} 内容不同。"
            ),
        )
    return _err("缺少比对目标", "提供 expect_hash(与哈希比对)或 compare_file(与文件比对)其一。")


# ────────────────────────────────────────────────────────────────────── #
# ③ 已有校验可视化(XFTP SHA512 已强制,这里把它显式呈现)
# ────────────────────────────────────────────────────────────────────── #

def simplex_file_info(path: str) -> dict[str, Any]:
    """报告一个已下载文件的哈希与 XFTP 内置校验状态。

    XFTP 在下载完成(rcvFileComplete)前已对文件 SHA512 强制校验 —— 能落盘就说明
    分片+整文件哈希都过了(否则下载中止)。本工具把"已验证一致"显式呈现:
    给出文件的 sha256/sha512,供与发送方 hash_file 发布的值人工/工具比对。
    """
    p = Path(path).expanduser()
    if not p.is_file():
        return _err(
            f"文件不存在: {p}",
            "确认已用 simplex_receive_file 下载完成,或给绝对路径。下载目录默认在 simplex files_folder。",
        )
    sha256 = _sha256_file(p)
    h512 = hashlib.sha512()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h512.update(chunk)
    return _ok(
        {
            "file": p.name,
            "size": p.stat().st_size,
            "sha256": sha256,
            "sha512": h512.hexdigest(),
            "xftp_integrity": "已通过 XFTP 内置 SHA512 校验(能下载落盘即代表分片与整文件哈希一致)",
        },
        diagnosable=(
            f"{p.name} 已经过 XFTP 传输层完整性校验。sha256={sha256[:16]}… "
            "若发送方经 hash_file/simplex_send_file_signed 发布了哈希,可用 hash_verify 比对确认与源文件完全一致。"
        ),
    )


# ────────────────────────────────────────────────────────────────────── #
# 工具注册
# ────────────────────────────────────────────────────────────────────── #

_TOOL_IMPLS: dict[str, Callable[..., dict[str, Any]]] = {
    "simplex_trust_establish": simplex_trust_establish,
    "simplex_trust_import": simplex_trust_import,
    "simplex_send_file_signed": simplex_send_file_signed,
    "simplex_verify_received_file": simplex_verify_received_file,
    "simplex_verify_file_by_manifest": simplex_verify_file_by_manifest,
    "simplex_file_info": simplex_file_info,
    "hash_file": hash_file,
    "hash_verify": hash_verify,
}


def get_tools() -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {
            "name": "simplex_trust_establish",
            "description": "与联系人建立文件签名信任根(把本端 Ed25519 公钥经 E2E 通道发给对方)。发送签名文件的前置。",
            "parameters": {"type": "object", "properties": {
                "contact": {"type": "string", "description": "联系人 display name 或 contact_id"}}, "required": ["contact"]}}},
        {"type": "function", "function": {
            "name": "simplex_trust_import",
            "description": "导入对方经带外渠道提供的信任根密钥,对齐双方签名验证(验证对方签名文件的前置)。",
            "parameters": {"type": "object", "properties": {
                "contact": {"type": "string"}, "key": {"type": "string", "description": "64 位十六进制密钥"}}, "required": ["contact", "key"]}}},
        {"type": "function", "function": {
            "name": "simplex_send_file_signed",
            "description": "发送文件 + Ed25519 签名清单(身份+出处)。接收方可验证『来自 X 且内容一致』。",
            "parameters": {"type": "object", "properties": {
                "contact": {"type": "string"}, "path": {"type": "string"},
                "caption": {"type": "string", "default": ""}}, "required": ["contact", "path"]}}},
        {"type": "function", "function": {
            "name": "simplex_verify_received_file",
            "description": "验证已接收文件的签名清单:重算 SHA256 比对 + 验签(Ed25519 优先,旧 HMAC 向后兼容)。",
            "parameters": {"type": "object", "properties": {
                "contact": {"type": "string"}, "path": {"type": "string"}}, "required": ["contact", "path"]}}},
        {"type": "function", "function": {
            "name": "simplex_verify_file_by_manifest",
            "description": "按文件名验证最近收到的文件的签名清单(IM 界面给收到的文件打『已验证』标用,不需本地路径)。",
            "parameters": {"type": "object", "properties": {
                "contact": {"type": "string"}, "file_name": {"type": "string"},
                "timeout": {"type": "number", "default": 15}}, "required": ["contact", "file_name"]}}},
        {"type": "function", "function": {
            "name": "simplex_file_info",
            "description": "报告已下载文件的 sha256/sha512 + XFTP 内置完整性校验状态(把已有的传输层校验可视化)。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "hash_file",
            "description": "计算任意文件哈希(默认 sha256)。用于经 IM 发布哈希供他人验证镜像/安装包。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "algorithm": {"type": "string", "default": "sha256"}}, "required": ["path"]}}},
        {"type": "function", "function": {
            "name": "hash_verify",
            "description": "校验文件一致性:与给定哈希比对(expect_hash)或与另一文件比对(compare_file)。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "expect_hash": {"type": "string", "default": ""},
                "compare_file": {"type": "string", "default": ""}, "algorithm": {"type": "string", "default": "sha256"}},
                "required": ["path"]}}},
    ]


def call_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    impl = _TOOL_IMPLS.get(name)
    if impl is None:
        return _err(f"未知工具 '{name}'", f"可用:{sorted(_TOOL_IMPLS)}")
    try:
        return impl(**args)
    except TypeError as e:
        return _err(f"参数错误:{e}", f"schema 见 get_tools()。")
    except Exception as e:  # noqa: BLE001
        return _err(f"工具 {name} 执行异常", f"{e!r}")


TOOL_NAMES = sorted(_TOOL_IMPLS)
