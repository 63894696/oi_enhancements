"""simplex_auto_accept.py — 「agent 帮加联系人」安全自动接受

功能
----
扫描一段文本，识别其中的 SimpleX 邀请链接（simplex:/invitation#... 或
https://<host>/i#... 应用链接），经**审批闸**后调用 simplex_accept_invitation
建立 E2E 加密联系。

信任边界（红线）
--------------
离线匿名工具的加联系人是**信任扩展动作**。绝不静默自动加陌生人：
- 命中链接后**必须过 policy_check_daemon**（照 simplex_send_message:187 模式）。
- verdict=deny → 直接拒绝，不发起。
- verdict=ask  → 需人工批准；standalone（无审批 UI）下本模块**不擅自批准**，
  返回 accepted=False + reason 提示需人工确认。集成进 daemon 审批层后由该层
  把 ask 升级为批准后再调用。
- verdict=allow → 才发起接受。

未接进 daemon 主循环（主循环位置未定位，属另一项工作）。本文件只提供可调用的
scan_and_accept() 函数 + CLI 入口。
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# policy_engine 与本模块同在 oi_enhancements;直接 import(同进程)。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from simplex_tools import call_tool, policy_check_daemon  # noqa: E402

# ── 邀请链接识别 ──────────────────────────────────────────────────── #
# 两种合法形态(见 simplex_tools._is_valid_conn_link:141):
#   1) simplex:/invitation#... 或 simplex:/contact#...   — 直接全链
#   2) https://<host>/i#... 或 https://<host>/invitation#/contact  — 应用短链/全链
# fragment(#...) 携带连接数据,必须存在。
_LINK_PATTERN = re.compile(
    r"(?:simplex:/(?:invitation|contact)#[^\s\"'<>]+"      # simplex:/xxx#<data>
    r"|https?://[^\s\"'<>]+/i#[^\s\"'<>]+"                   # https://host/i#<data>
    r"|https?://[^\s\"'<>]+/invitation#[^\s\"'<>]+"          # https://host/invitation#<data>
    r"|https?://[^\s\"'<>]+/contact#[^\s\"'<>]+)",           # https://host/contact#<data>
    re.IGNORECASE,
)


def extract_invitation_link(text: str) -> str | None:
    """从文本中提取第一个 SimpleX 邀请/名片链接。无则返回 None。"""
    if not text or not isinstance(text, str):
        return None
    m = _LINK_PATTERN.search(text)
    if not m:
        return None
    # 去除尾部常见标点(中英文句号/逗号/括号等可能紧贴链接)
    return m.group(0).rstrip(".,;:!?)]}\"'。,;:!?】)」』")


# ── 本地确认白名单(双保险第二道)──────────────────────────────────── #
# 即便 policy 层 allow(如已批准同类规则),一个「首次见到」的链接也绝不自动
# 接受——必须由人工显式 confirm_invitation() 登记后才放行。这样即使上层审批
# 被绕过/配置成 allow,陌生链接仍会被本层拦下。已知链接集内存持有 + 可选
# 文件持久化(SIMPLEX_ACCEPT_WHITELIST 环境变量指定路径)。
_WHITELIST_PATH = os.environ.get("SIMPLEX_ACCEPT_WHITELIST", "").strip()


def _load_confirmed() -> set[str]:
    if not _WHITELIST_PATH:
        return set()
    try:
        data = json.loads(Path(_WHITELIST_PATH).read_text(encoding="utf-8"))
        return set(data.get("confirmed", []))
    except Exception:  # noqa: BLE001 — 文件缺失/损坏 → 空集(fail-closed:全部首见)
        return set()


def _save_confirmed(confirmed: set[str]) -> None:
    if not _WHITELIST_PATH:
        return
    try:
        Path(_WHITELIST_PATH).write_text(
            json.dumps({"confirmed": sorted(confirmed)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001 — 持久化失败不阻断流程(内存集仍生效)
        pass


_CONFIRMED_LINKS: set[str] = _load_confirmed()


def confirm_invitation(link: str) -> dict:
    """人工登记:把一个链接标记为「已确认可接受」。返回登记结果。

    这是「人工批准」的落点:集成审批 UI / 人工核对后调用本函数,再重新
    scan_and_accept 即会放行(经 policy 层)。返回 {ok, link, already}。
    """
    if not link or not isinstance(link, str):
        return {"ok": False, "link": link, "error": "空链接"}
    already = link in _CONFIRMED_LINKS
    if not already:
        _CONFIRMED_LINKS.add(link)
        _save_confirmed(_CONFIRMED_LINKS)
    return {"ok": True, "link": link, "already": already,
            "count": len(_CONFIRMED_LINKS)}


def is_confirmed(link: str) -> bool:
    """该链接是否已被人工确认过。"""
    return link in _CONFIRMED_LINKS


def scan_and_accept(text: str) -> dict:
    """扫描文本,经审批后接受其中的 SimpleX 邀请链接。

    返回:
      {found: bool, link: str|None, accepted: bool, reason: str}
    """
    link = extract_invitation_link(text)
    if link is None:
        return {"found": False, "link": None, "accepted": False,
                "reason": "文本中未发现 SimpleX 邀请链接"}

    # ── 审批闸(信任边界,禁止静默自动加陌生人)──────────────────── #
    verdict, preason = policy_check_daemon(
        "simplex_accept_invitation", {"link": link})

    if verdict == "deny":
        return {"found": True, "link": link, "accepted": False,
                "reason": f"审批拒绝:{preason}"}

    if verdict == "ask":
        # standalone 无审批 UI;不擅自批准,等待人工确认后由调用方重新发起。
        msg = ("需人工批准(ask:{0});"
               "请人工确认后调用 simplex_accept_invitation(link)").format(preason)
        return {"found": True, "link": link, "accepted": False, "reason": msg}

    # verdict == "allow" → 进入本地白名单闸(第二道保险:首见链接仍需人工确认)
    if not is_confirmed(link):
        return {"found": True, "link": link, "accepted": False,
                "reason": ("policy 层已放行,但该链接为首次见到,"
                           "需人工 confirm_invitation(link) 确认后才接受;绝不自动加陌生人"),
                "needs_confirmation": True}

    # 已确认 + policy allow → 发起接受
    result = call_tool("simplex_accept_invitation", {"link": link})
    if result.get("ok"):
        return {"found": True, "link": link, "accepted": True,
                "reason": result.get("diagnosable", "已建立联系"),
                "result": result}
    # 失败(含 ContactAlreadyExists 等诊断)直接透传
    return {"found": True, "link": link, "accepted": False,
            "reason": result.get("error", "接受失败"),
            "diagnosable": result.get("diagnosable", ""),
            "result": result}


def main(argv: list[str]) -> int:
    """CLI 入口:读 argv 或 stdin 文本,打印结果。"""
    if len(argv) > 1:
        text = " ".join(argv[1:])
    else:
        text = sys.stdin.read()
    result = scan_and_accept(text)
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("accepted") else (1 if not result.get("found") else 2)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
