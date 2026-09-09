"""tests/prisiragent_cli_db_test.py — CLI 会话库定位 + 与 web 端同库单元测试(档位1+2)。

覆盖 prisiragent_cli._chat_db_path() 的改动(CLI 从旧的
%LOCALAPPDATA%/PrisirAI/chat.db 迁到与 web 同源的 ~/.local/share/prisir/chats.db):

  - 显式覆盖优先: PRISIRAGENT_CHAT_DB / OIAGENT_CHAT_DB(旧别名仍生效),
    且父目录不存在时自动创建(否则 sqlite3.connect 直接 OperationalError)
  - PRISIR_DATA 覆盖 → <PRISIR_DATA>/chats.db,与 prisiragent_web._CHAT_DB 完全一致
  - 默认兜底 → ~/.local/share/prisir/chats.db(不再落 LOCALAPPDATA/PrisirAI/chat.db)
  - schema 平价: CLI 建的库与 web 建的库 sqlite_master 逐条相同
    (sessions / messages / idx_msg_sess),否则「互相续聊」会炸
  - 双向可读: CLI 写的会话 web 读得到,web 写的会话 CLI 读得到(含 followups JSON)

全程用临时目录,不碰真实 chats.db。跑法:
  python tests/prisiragent_cli_db_test.py  →  打印 PASS/FAIL
"""
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

# 必须在 import 之前设好:prisiragent_web 在模块级就读 PRISIR_DATA 定 _CHAT_DB
_tmp = tempfile.mkdtemp(prefix="prisir_cli_db_test_")
os.environ["PRISIR_DATA"] = _tmp
# 清掉可能来自外部环境的覆盖,保证测试可重复
for _k in ("PRISIRAGENT_CHAT_DB", "OIAGENT_CHAT_DB"):
    os.environ.pop(_k, None)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import prisiragent_cli as c  # noqa: E402
import prisiragent_web as w  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_fails = []


def check(name, cond):
    print(("✓ " if cond else "✗ ") + name)
    if not cond:
        _fails.append(name)


def _norm(p):
    return os.path.normcase(os.path.normpath(str(p)))


def _schema(db_path):
    """取一个库的 sqlite_master 指纹(类型+名字+压平后的 SQL),排序后返回。"""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL").fetchall()
    finally:
        con.close()
    return sorted((t, n, re.sub(r"\s+", " ", s).strip()) for t, n, s in rows)


def test_explicit_override_wins_and_creates_parent():
    """PRISIRAGENT_CHAT_DB 优先于一切,且嵌套父目录会被建出来。"""
    nested = os.path.join(_tmp, "ovr", "a", "b", "cli.db")
    os.environ["PRISIRAGENT_CHAT_DB"] = nested
    try:
        got = c._chat_db_path()
        check("PRISIRAGENT_CHAT_DB 覆盖生效", _norm(got) == _norm(nested))
        check("覆盖路径的父目录被自动创建", os.path.isdir(os.path.dirname(nested)))
        # 真能打开并建表(父目录缺失时这一步会 OperationalError)
        con = c._db()
        con.close()
        check("覆盖路径可直接建库建表", os.path.isfile(nested))
    finally:
        os.environ.pop("PRISIRAGENT_CHAT_DB", None)


def test_legacy_alias_still_honoured():
    """OIAGENT_CHAT_DB 是旧别名,不能因为改名而失效。"""
    legacy = os.path.join(_tmp, "legacy_alias.db")
    os.environ["OIAGENT_CHAT_DB"] = legacy
    try:
        check("OIAGENT_CHAT_DB 旧别名仍生效", _norm(c._chat_db_path()) == _norm(legacy))
    finally:
        os.environ.pop("OIAGENT_CHAT_DB", None)


def test_prisir_data_parity_with_web():
    """PRISIR_DATA 下 CLI 与 web 必须指向同一个文件 —— 这是「互相续聊」的前提。"""
    cli_p = c._chat_db_path()
    web_p = w._CHAT_DB
    check("CLI 路径 = <PRISIR_DATA>/chats.db",
          _norm(cli_p) == _norm(os.path.join(_tmp, "chats.db")))
    check("CLI 与 web 指向同一个库文件", _norm(cli_p) == _norm(web_p))
    check("库文件名为 chats.db(非旧 chat.db)", os.path.basename(cli_p) == "chats.db")


def test_default_fallback_is_shared_user_data():
    """无 PRISIR_DATA 时兜底到 ~/.local/share/prisir/chats.db,不再落 LOCALAPPDATA。

    用假 HOME 隔离,避免测试在真实用户目录里建文件夹。
    """
    fake_home = os.path.join(_tmp, "fakehome")
    # 连 PRISIR_DATA 一起暂存并清掉——本用例测的是「无任何覆盖时的纯兜底」,
    # 顶部为隔离 web 设的 PRISIR_DATA 必须摘掉,否则 _chat_db_path 走 PRISIR_DATA 分支。
    saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "PRISIR_DATA")}
    os.environ["HOME"] = fake_home
    os.environ["USERPROFILE"] = fake_home
    os.environ.pop("PRISIR_DATA", None)
    try:
        got = c._chat_db_path()
        want = os.path.join(fake_home, ".local", "share", "prisir", "chats.db")
        check("默认兜底 = ~/.local/share/prisir/chats.db", _norm(got) == _norm(want))
        check("默认兜底目录被创建", os.path.isdir(os.path.dirname(want)))
        check("默认兜底不再用 LOCALAPPDATA/PrisirAI/chat.db",
              "PrisirAI" not in got and os.path.basename(got) != "chat.db")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_schema_parity_cli_vs_web():
    """CLI 建的库和 web 建的库 schema 必须逐条一致(注释里写了 keep in sync)。

    故意让两边落在不同文件上,才能比出「各自建表」的结果是否相同。
    """
    cli_only = os.path.join(_tmp, "schema_cli", "chats.db")
    web_only = _norm(w._CHAT_DB)
    os.environ["PRISIRAGENT_CHAT_DB"] = cli_only
    try:
        con = c._db()
        con.close()
        # web 端库由前面的读写用例触发创建;这里确保它存在
        wcon = w._db()
        wcon.close()
        cs, ws = _schema(cli_only), _schema(web_only)
        # sql IS NOT NULL 会带上 sqlite_sequence(AUTOINCREMENT 副产物),故每库是 4 条
        # (sessions/messages/sqlite_sequence 三表 + idx_msg_sess 一索引),不是注释里的 3。
        check("两边都建出了 4 条对象(3 表 + 1 索引)", len(cs) == 4 and len(ws) == 4)
        check("CLI/web schema 完全一致", cs == ws)
        if cs != ws:
            print("   cli:", cs)
            print("   web:", ws)
        names = {n for _, n, _ in cs}
        check("含 sessions 表", "sessions" in names)
        check("含 messages 表", "messages" in names)
        check("含 idx_msg_sess 索引", "idx_msg_sess" in names)
    finally:
        os.environ.pop("PRISIRAGENT_CHAT_DB", None)


def test_cross_readable_both_directions():
    """同库双向可读:CLI 写 → web 读;web 写 → CLI 读(followups JSON 也要对)。"""
    check("前置:CLI 与 web 同库", _norm(c._chat_db_path()) == _norm(w._CHAT_DB))

    sid_cli = c.create_session("CLI 建的会话")
    c.add_message(sid_cli, "user", "从 CLI 写一条")
    c.add_message(sid_cli, "assistant", "CLI 回复", followups=["追问1", "追问2"])

    row = w.get_session(sid_cli)
    check("web 读得到 CLI 建的会话", row is not None and row[1] == "CLI 建的会话")
    wmsgs = w.get_messages(sid_cli)
    check("web 读得到 CLI 写的 2 条消息", len(wmsgs) == 2)
    check("web 侧 role 顺序 user→assistant",
          [m["role"] for m in wmsgs] == ["user", "assistant"])
    check("web 侧 followups 正确反序列化",
          wmsgs[-1]["followups"] == ["追问1", "追问2"])

    sid_web = w.create_session("Web 建的会话")
    w.add_message(sid_web, "user", "从 web 写一条")
    w.add_message(sid_web, "assistant", "web 回复")
    cmsgs = c.get_messages(sid_web)
    check("CLI 读得到 web 写的消息",
          [(m["role"], m["content"]) for m in cmsgs] ==
          [("user", "从 web 写一条"), ("assistant", "web 回复")])
    check("CLI 读得到 web 建的会话标题",
          (c.get_session(sid_web) or (None, None))[1] == "Web 建的会话")


def main():
    print("=== prisiragent_cli 会话库定位 / CLI-web 同库测试 ===")
    print(f"PRISIR_DATA = {_tmp}\n")
    test_explicit_override_wins_and_creates_parent()
    print()
    test_legacy_alias_still_honoured()
    print()
    test_prisir_data_parity_with_web()
    print()
    test_default_fallback_is_shared_user_data()
    print()
    test_schema_parity_cli_vs_web()
    print()
    test_cross_readable_both_directions()
    print("\n=== 判定 ===")
    if _fails:
        print(f"FAIL: {len(_fails)} 项未过 -> {_fails}")
        return 1
    print("PASS: 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
