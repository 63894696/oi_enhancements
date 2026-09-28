"""prisirmp CLI 子命令实现。

各命令的\"实现层\"逐步在 extract.py / engine.py 里落,这里只负责
参数解析 + 调用 + 人话格式化输出。

失败兜底: 任何子命令报错返 rc=1 但不抛栈(用户友好)。
"""
from __future__ import annotations

import argparse
import json as _json
import sys
from typing import Sequence

from . import __version__
from .engine import Mprecaller


_HELP = "prisirmp — 个人微信公众号历史 recall(独立 CLI)"

_EPILOG = """\
示例:
  python -m prisir_mp status
  python -m prisir_mp search "学习 Python"
  python -m prisir_mp verify
"""


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="prisirmp", description=_HELP, epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version",
                   version=f"prisirmp {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="列出账号 / 已索引文章数 / last_index")
    sub.add_parser("clear", help="清空文章索引(保留账号配置)")
    sub.add_parser("verify", help="跑 E2E 自检")

    # extract / decrypt / index — Phase 0 落地
    sub.add_parser("extract", help="调 wcdb-key-tool extract 拿密钥")

    p_dec = sub.add_parser("decrypt", help="调 wcdb-key-tool 解密 Message*.db 到本地缓存")
    p_dec.add_argument("--src", help="微信 db 所在目录(默认自动探测)")
    p_dec.add_argument("--dst", help="解密后缓存目录(默认 ~/.local/share/prisirmp/cache/<account_id>/)")
    p_dec.add_argument("--key", help="32 字节 hex 密钥(已 extract 过则可直接传)")

    p_idx = sub.add_parser("index", help="解析解密后的 Message*.db → 入库 + FTS5")
    p_idx.add_argument("--decrypted-dir",
                       help="decrypt 出的缓存目录;不传则按 accounts 表里最新那条")
    p_idx.add_argument("--account-id", help="账号 ID(默认 md5(decrypted_dir))")

    p_search = sub.add_parser("search", help="按关键词搜公众号文章")
    p_search.add_argument("query", help="搜索关键词")
    p_search.add_argument("--limit", type=int, default=20,
                          help="最多返回条数(默认 20,上限 200)")
    p_search.add_argument("--json", action="store_true", help="JSON 输出")

    return p


def _print_status(st: dict) -> None:
    print(f"prisirmp {__version__}")
    print(f"  索引库:     {st.get('indexed_count', 0)} 条公众号文章")
    print(f"  已登录账号: {st.get('account_count', 0)} 个")
    last_index = st.get('last_index', 0)
    if last_index:
        import datetime
        ts = datetime.datetime.fromtimestamp(last_index).strftime("%Y-%m-%d %H:%M:%S")
        print(f"  最近索引:   {ts}")
    for a in st.get('accounts', []):
        import datetime
        ts = datetime.datetime.fromtimestamp(a['last_decrypt']).strftime("%Y-%m-%d %H:%M:%S") if a['last_decrypt'] else '-'
        print(f"    - {a['account_id'][:8]}…  消息 {a['message_count']}  解密 {ts}")
    if not st.get('enabled'):
        print("\n  状态: 未启用。先跑 extract → decrypt → index(待 Phase 0 完成)。")


def _print_search(hits: list, total: int, query: str) -> None:
    print(f"匹配 {total} 条,展示 {len(hits)} 条:")
    import datetime
    for i, h in enumerate(hits, 1):
        ts = datetime.datetime.fromtimestamp(h['ts']).strftime("%Y-%m-%d") if h['ts'] else '----'
        pub = h.get('publisher') or '?'
        # snippet 里的 ** ** 高亮 → ANSI 反色块(终端友好)
        snip = h.get('snippet') or ''
        if snip:
            snip = snip.replace('**', '\x1b[7m', 1)
            while '**' in snip:
                snip = snip.replace('**', '\x1b[0m', 1)
                if '**' in snip:
                    snip = snip.replace('**', '\x1b[7m', 1)
        print(f"\n[{i}] {h['title']}")
        print(f"    {pub}  ·  {ts}")
        print(f"    {h['url']}")
        if snip:
            print(f"    …{snip}…")


def cmd_status(_args: argparse.Namespace) -> int:
    st = Mprecaller.shared().status()
    _print_status(st)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    rc = Mprecaller.shared()
    res = rc.search(args.query, limit=args.limit)
    if args.json:
        print(_json.dumps(res, ensure_ascii=False, indent=2))
        return 0
    _print_search(res["hits"], res["total"], args.query)
    return 0


def cmd_clear(_args: argparse.Namespace) -> int:
    rc = Mprecaller.shared()
    rc.clear_articles()
    print("已清空文章索引。账号配置保留。")
    return 0


def cmd_extract(_args: argparse.Namespace) -> int:
    from .extract import wcdb_extract, find_wcdb_key_tool, WcdbNotFoundError
    bin_path = find_wcdb_key_tool()
    if not bin_path:
        print("[error] 未找到 wcdb-key-tool 二进制。", file=sys.stderr)
        print("下载指引: https://github.com/TANGandXUE/wcdb-key-tool/releases", file=sys.stderr)
        print("放到 PATH 或 ~/.local/share/prisirmp/toolbin/ 下。", file=sys.stderr)
        return 2
    try:
        key = wcdb_extract(bin_path)
    except WcdbNotFoundError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"[error] wcdb extract 失败: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    # 把 key 写到本地 keychain(meta 表),不入库原文
    Mprecaller.shared()._set_meta("wcdb_key", key)
    print(f"[ok] 已拿 key(64 hex),存入本地 keychain 索引(下次 decrypt 可自动用)。")
    print(f"      key: {key[:8]}…{key[-4:]}  ({len(key)} chars)")
    return 0


def cmd_decrypt(args: argparse.Namespace) -> int:
    from .extract import wcdb_decrypt, find_wcdb_key_tool, WcdbNotFoundError
    import getpass
    import hashlib
    import os as _os

    src = args.src
    if not src:
        # 自动探测:Win %USERPROFILE%\Documents\xwechat_files;Linux ~/xwechat_files
        if sys.platform == "win32":
            src = _os.path.join(_os.environ.get("USERPROFILE", ""), "Documents", "xwechat_files")
        else:
            src = _os.path.join(_os.path.expanduser("~"), "xwechat_files")
    if not _os.path.isdir(src):
        print(f"[error] 微信 db 目录不存在: {src}", file=sys.stderr)
        print("       请用 --src 显式指定(里面应有子目录 <wxid>/db/ 或 Message_*.db)。",
              file=sys.stderr)
        return 2

    key = args.key or Mprecaller.shared()._get_meta("wcdb_key", "")
    if not key:
        print("[error] 缺密钥。先跑 `extract` 拿 key,或用 --key 传入 64 位 hex。",
              file=sys.stderr)
        return 2

    dst = args.dst
    if not dst:
        account_id = hashlib.md5(src.encode("utf-8")).hexdigest()[:16]
        if sys.platform == "win32":
            base = _os.path.join(_os.environ.get("LOCALAPPDATA", ""), "prisirmp", "cache")
        else:
            base = _os.path.join(_os.path.expanduser("~"), ".local", "share", "prisirmp", "cache")
        dst = _os.path.join(base, account_id)

    bin_path = find_wcdb_key_tool()
    if not bin_path:
        print("[error] 未找到 wcdb-key-tool。", file=sys.stderr)
        return 2
    try:
        rc = wcdb_decrypt(bin_path, key, src, dst)
    except WcdbNotFoundError as e:
        print(f"[error] {e}", file=sys.stderr)
        return 2
    if rc != 0:
        print(f"[error] wcdb decrypt rc={rc},详细日志见 stderr。", file=sys.stderr)
        return 1
    # 记账号
    account_id = hashlib.md5(src.encode("utf-8")).hexdigest()[:16]
    Mprecaller.shared().upsert_account(account_id, src, dst)
    print(f"[ok] 解密完成 → {dst}")
    print(f"      后续 `python -m prisir_mp index` 即可解析入库。")
    return 0


def cmd_index(args: argparse.Namespace) -> int:
    """解析解密后的 Message*.db 里 Type=49 appmsg → 入库。

    流程:
      1) 找解密目录(wcdb-decrypt 出来的,内含解密的 SQLite db)
      2) 逐 db 读 Message 表 Type=49 行,XML 字段过 parse_appmsg_xml
      3) 批量入 articles + articles_fts
    """
    import glob as _glob
    import os as _os
    import sqlite3 as _sq

    from .extract import parse_appmsg_xml

    rc_engine = Mprecaller.shared()

    dst = args.decrypted_dir
    account_id = args.account_id or ""
    if not dst:
        # 取 accounts 表里最新一条
        st = rc_engine.status()
        accounts = st.get("accounts", [])
        if not accounts:
            print("[error] 还没解密过任何账号。先跑 `decrypt`。", file=sys.stderr)
            return 2
        latest = accounts[0]
        dst = latest["decrypted_dir"]
        account_id = latest["account_id"]
        print(f"[info] 自动选最新账号: {account_id[:8]}… ({dst})")

    if not _os.path.isdir(dst):
        print(f"[error] 解密目录不存在: {dst}", file=sys.stderr)
        return 2
    # 找 Message*.db(解密后 SQLite db 一般以 Message_*.db 或 message.db 存在)
    dbs = []
    for pat in ("Message_*.db", "message_*.db", "Message.db", "message.db"):
        dbs.extend(_glob.glob(_os.path.join(dst, "**", pat), recursive=True))
        if dbs:
            break
    if not dbs:
        print(f"[error] 在 {dst} 下找不到任何 Message*.db(微信解密后 db)。",
              file=sys.stderr)
        print("       请确认 decrypt 命令已成功、产物目录里有解密的 db 文件。",
              file=sys.stderr)
        return 2

    total_n = 0
    for db_path in dbs:
        print(f"[scan] {db_path}")
        try:
            conn = _sq.connect(db_path, timeout=30)
            conn.text_factory = bytes  # 全部以 bytes 返回,XML 字段单独解
        except Exception as e:
            print(f"  [skip] 无法打开 db: {e}", file=sys.stderr)
            continue
        batch = []
        try:
            # 微信 Message 表结构: Type / CreateTime / Content(内含 appmsg XML)
            cur = conn.execute(
                "SELECT Type, CreateTime, Content, FromUserName "
                "FROM Message WHERE Type=49")
            for row in cur:
                try:
                    _type, create_time, content_bytes, from_user = row
                except Exception:
                    continue
                if not content_bytes:
                    continue
                # content_bytes 可能是 <msg>...</msg> 整包 XML
                # parse_appmsg_xml 内部找 <appmsg>,不用额外剥 <msg>
                meta = parse_appmsg_xml(content_bytes)
                if not meta:
                    continue
                meta["account_id"] = account_id
                meta["ts"] = int(create_time or 0) * 1000 if create_time else 0
                meta["text"] = meta.get("desc", "")
                batch.append(meta)
                if len(batch) >= 500:
                    n = rc_engine.upsert_articles(batch)
                    total_n += n
                    batch.clear()
                    print(f"  · 已入库 {total_n} …", end="\r")
        except Exception as e:
            print(f"  [warn] 扫描 {db_path} 异常: {type(e).__name__}: {e}",
                  file=sys.stderr)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if batch:
            n = rc_engine.upsert_articles(batch)
            total_n += n
            batch.clear()
    print(f"\n[ok] 本次解析累计入库 {total_n} 条公众号文章。")
    return 0


def cmd_verify(_args: argparse.Namespace) -> int:
    # 委托给 verify.py(同包模块)——会走自己的 sys.exit
    try:
        from . import verify as _verify  # noqa: PLC0415
    except Exception as e:
        print(f"[verify] 加载失败: {e}", file=sys.stderr)
        return 1
    return int(_verify.main() or 0)


def run(argv: Sequence[str]) -> int:
    argv = list(argv)
    # 让 `python -m prisir_mp <cmd>` 第一项(模块路径)自动跳过
    if argv and argv[0].endswith("__main__.py"):
        argv = argv[1:]
    if argv and argv[0] == "prisirmp":
        argv = argv[1:]
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as e:
        return int(e.code) if e.code is not None else 2

    handlers = {
        "status": cmd_status,
        "search": cmd_search,
        "clear": cmd_clear,
        "verify": cmd_verify,
        "extract": cmd_extract,
        "decrypt": cmd_decrypt,
        "index": cmd_index,
    }
    handler = handlers.get(args.cmd)
    if not handler:
        print(f"未知命令: {args.cmd}", file=sys.stderr)
        return 2
    try:
        return int(handler(args) or 0)
    except KeyboardInterrupt:
        print("\n[abort] 用户中断", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 — 兜底不抛栈
        print(f"[error] {type(e).__name__}: {e}", file=sys.stderr)
        return 1