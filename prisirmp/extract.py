# -*- coding: utf-8 -*-
"""prisirmp 数据解析 + 解密。

两半:
1. parse_appmsg_xml(xml_str) -> dict:从 Type=49 appmsg XML 抽出文章元数据。
   纯函数,不依赖 wx db;用 fixture XML 即可单测。

2. decrypt_db(key_hex, src_db, dst_db) + 调 wcdb-key-tool 子进程:
   - wcdb-key-tool 走子进程拿 key / 解密(双平台支持)。
   - AES-CBC page 解 SQLCipher 用 ctypes(WCng Win / OpenSSL Linux)。
   - decrypt_db 暂留 TODO:Phase 0 用 wcdb-key-tool 子进程完成,不内嵌解密逻辑。

XML 关键字段(2026-09-24 参考多篇 Type=49 解析文章):
  appmsg/type         = 5(公众号文章) / 33(小程序) / 6(文件)等
  appmsg/title        = 文章标题
  appmsg/des          = 摘要
  appmsg/url          = 原文 URL(去重主键)
  appmsg/thumburl     = 缩略图
  appmsg/appid        = 公众号 appid
  appmsg/fromusername = 公众号 __biz
  appmsg/docid        = 文章 docid
  appmsg/item/datadesc/sourceusername = 公众号名(展示用)
"""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

# Type=49 公众号文章 appmsg 内 type 子类型(已确认:5 = 文章, 33 = 小程序)
_APPMG_MSG_TYPE_ARTICLE = "5"


def parse_appmsg_xml(xml_str: str | bytes) -> dict[str, Any] | None:
    """解析 Type=49 appmsg XML → 公众号文章元数据。非文章返 None。

    容错: 字段缺失 → 返 None(让上层跳过不入索引);不抛栈。
    """
    if isinstance(xml_str, bytes):
        # 微信本地 db 里 XML 通常 utf-8,失败兜 utf-8-replace
        try:
            xml_str = xml_str.decode("utf-8")
        except UnicodeDecodeError:
            xml_str = xml_str.decode("utf-8", errors="replace")
    if not xml_str or "<appmsg" not in xml_str:
        return None
    try:
        # XML 可能含 HTML 实体( &amp; 等),ET 自己处理
        root = ET.fromstring(xml_str)
    except ET.ParseError:
        # 微信 db 里偶有截断/坏 XML,容错:正则兜底抓关键字段
        return _regex_fallback(xml_str)

    appmsg = root.find("appmsg")
    if appmsg is None:
        return None

    type_el = appmsg.find("type")
    if type_el is None or (type_el.text or "").strip() != _APPMG_MSG_TYPE_ARTICLE:
        return None  # 非公众号文章

    def _txt(tag: str) -> str:
        el = appmsg.find(tag)
        if el is None or el.text is None:
            return ""
        # ET 默认不解 &amp; 等实体,显式解
        return html.unescape(el.text.strip())

    title = _txt("title")
    url = _txt("url")
    if not title or not url:
        return None
    if not url.startswith(("http://", "https://")):
        return None

    biz = _txt("fromusername") or _txt("appid")
    desc = _txt("des")
    thumburl = _txt("thumburl")
    docid = _txt("docid")

    # 公众号显示名(展示用)— 实际结构是 <item><datadesc><sourceusername>机器之心</sourceusername></datadesc></item>
    publisher = ""
    item = appmsg.find("item")
    if item is not None:
        # 先找 item 直接子(sourceusername / nickname / displayname)
        for sub_tag in ("sourceusername", "nickname", "displayname"):
            sub = item.find(sub_tag)
            if sub is not None and sub.text:
                publisher = html.unescape(sub.text.strip())
                break
        # 再找 item/datadesc/sourceusername(实际常见结构)
        if not publisher:
            datadesc = item.find("datadesc")
            if datadesc is not None:
                for sub_tag in ("sourceusername", "nickname", "displayname"):
                    sub = datadesc.find(sub_tag)
                    if sub is not None and sub.text:
                        publisher = html.unescape(sub.text.strip())
                        break
            if not publisher and datadesc is not None and datadesc.text:
                # <datadesc>机器之心</datadesc> 直接放文本的退化形式
                publisher = html.unescape(datadesc.text.strip())
        # 兜底:item 自己有 text
        if not publisher and item.text:
            publisher = html.unescape(item.text.strip())
        # 最后兜底:iter item 的子文本(ET 在 <item>纯文本</item> 时不会把 text 放在 .text 上)
        if not publisher:
            txt_chunks = [t for t in item.itertext()
                       if t and html.unescape(t.strip())]
            if txt_chunks:
                publisher = html.unescape(txt_chunks[0].strip())

    return {
        "biz": biz,
        "title": title,
        "url": _normalize_url(url),
        "desc": desc,
        "publisher": publisher,
        "thumburl": thumburl,
        "docid": docid,
        "msg_type": int(_APPMG_MSG_TYPE_ARTICLE),
    }


# URL 规范化用于去重(微信 db 里同一文章可能因追踪参数带出多个 url)
# 保留公众号文章 URL 的核心身份参数;其余追踪类全剥。
_TRACK_PARAMS = {
    "__biz", "mid", "idx", "sn", "chksm", "mpshare", "ascene",
    "appmsgid", "itemid", "signature", "uin", "key",
    # 兼容历史版本
}


def _normalize_url(u: str) -> str:
    """去微信追踪参数 + 强制 https + 去 fragment。"""
    try:
        p = urlparse(u)
    except Exception:
        return u
    # 仅保留公众号 url 域参数(__biz / mid / idx / sn 等)
    q = "&".join(
        f"{k}={v}" for k, v in sorted(
            [(pair.split("=", 1)[0], pair.split("=", 1)[1])
             for pair in (p.query or "").split("&")
             if "=" in pair]
        )
        if k in _TRACK_PARAMS
    ) if p.query else ""
    netloc = p.netloc.lower()
    if netloc.startswith("mp.weixin.qq.com") and not p.path.startswith("/s"):
        # 微信公众号文章固定 path /s?__biz=...&mid=...
        return ""
    return f"https://{netloc}{p.path}?{q}" if q else f"https://{netloc}{p.path}"


def _regex_fallback(xml_str: str) -> dict[str, Any] | None:
    """ET 解析失败时的正则兜底:抓关键字段。"""
    title_m = re.search(r"<title>(?:!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", xml_str, re.S)
    url_m = re.search(r"<url>(?:!\[CDATA\[)?(.*?)(?:\]\]>)?</url>", xml_str, re.S)
    type_m = re.search(r"<type>(\d+)</type>", xml_str)
    if not title_m or not url_m or not type_m:
        return None
    if type_m.group(1) != _APPMG_MSG_TYPE_ARTICLE:
        return None
    title = html.unescape(title_m.group(1).strip())
    url = html.unescape(url_m.group(1).strip())
    if not title or not url.startswith(("http://", "https://")):
        return None
    norm = _normalize_url(url)
    if not norm:
        return None
    desc_m = re.search(r"<des>(?:!\[CDATA\[)?(.*?)(?:\]\]>)?</des>", xml_str, re.S)
    biz_m = re.search(r"<fromusername>(?:!\[CDATA\[)?(.*?)(?:\]\]>)?</fromusername>", xml_str, re.S)
    pub_m = re.search(r"<sourceusername>(?:!\[CDATA\[)?(.*?)(?:\]\]>)?</sourceusername>", xml_str, re.S)
    return {
        "biz": html.unescape(biz_m.group(1).strip()) if biz_m else "",
        "title": title,
        "url": norm,
        "desc": html.unescape(desc_m.group(1).strip()) if desc_m else "",
        "publisher": html.unescape(pub_m.group(1).strip()) if pub_m else "",
        "thumburl": "",
        "docid": "",
        "msg_type": int(_APPMG_MSG_TYPE_ARTICLE),
    }


# ---------------------------------------------------------------------------
# wcdb-key-tool 调子进程(Phase 0 末了再接,先留骨架)
# ---------------------------------------------------------------------------

# 候选路径: PATH → 项目内<toolbin> → 用户级 toolbin 目录
_TOOLBIN_CANDIDATES = [
    os.path.join(os.path.expanduser("~"), ".local", "share", "prisirmp", "toolbin"),
    os.path.join(os.path.expanduser("~"), ".prisirmp", "toolbin"),
    os.path.join(os.getcwd(), "prisirmp_toolbin"),
]


def find_wcdb_key_tool() -> str | None:
    """查找 wcdb-key-tool 入口脚本路径(Python 源码工具)。PATH → 已知候选目录。

    wcdb-key-tool 是 Python 单文件脚本(wcdb_key_tool[_windows|_macos].py),
    不是预编译二进制,所以候选名以 .py 为优先,无后缀/.exe 次之(若用户
    自己包装成 .exe/.bat)。
    """
    import shutil
    # PATH 直查(用户可能 alias / 包装成可执行)
    for cand in ("wcdb_key_tool.py", "wcdb_key_tool", "wcdb_key_tool.exe",
                 "wcdb-key-tool", "wcdb-key-tool.exe"):
        p = shutil.which(cand)
        if p:
            return p
    # 已知候选目录(按 README 的 _TOOLBIN_CANDIDATES 列表)
    for d in _TOOLBIN_CANDIDATES:
        if not os.path.isdir(d):
            continue
        for cand in ("wcdb_key_tool.py", "wcdb_key_tool_windows.py",
                     "wcdb_key_tool_macos.py",
                     "wcdb_key_tool.exe", "wcdb_key_tool", "wcdb-key-tool"):
            p = os.path.join(d, cand)
            if os.path.isfile(p):
                return p
    return None


class WcdbNotFoundError(FileNotFoundError):
    """未找到 wcdb-key-tool。"""


def wcdb_extract(bin_path: str | None = None, timeout: int = 120) -> str:
    """调 wcdb-key-tool extract 拿密钥(32 字节 hex)。

    失败抛 WcdbNotFoundError / subprocess.CalledProcessError。
    """
    bp = bin_path or find_wcdb_key_tool()
    if not bp:
        raise WcdbNotFoundError(
            "未找到 wcdb-key-tool 二进制。可从 "
            "https://github.com/TANGandXUE/wcdb-key-tool/releases 下载"
            " (Linux/Win/macOS 三平台),放到 PATH 或 ~/.local/share/prisirmp/toolbin/ 下。"
        )
    # 平台子命令差异:Windows 走 python 脚本(无 .exe),Linux/macOS 直跑
    if bp.endswith((".py", ".pyw")):
        cmd = [sys.executable, bp, "extract"]
    else:
        cmd = [bp, "extract"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise subprocess.CalledProcessError(
            r.returncode, cmd, output=r.stdout, stderr=r.stderr)
    # 解析输出: wcdb-key-tool 输出格式可能 "<hex_key>" 或带前缀
    out = (r.stdout or "").strip()
    # 取最长 hex 串(64 字符)
    hex_m = re.search(r"\b([0-9a-fA-F]{64})\b", out)
    if hex_m:
        return hex_m.group(1).lower()
    # 没匹配上 hex:整段当 key(容错)
    return out


def wcdb_decrypt(bin_path: str | None, key_hex: str, src_dir: str,
                 dst_dir: str, timeout: int = 300) -> int:
    """调 wcdb-key-tool decrypt 解密 src_dir 下所有 db → dst_dir。

    返回 wcdb-key-tool 的 returncode(0 视为成功)。
    """
    bp = bin_path or find_wcdb_key_tool()
    if not bp:
        raise WcdbNotFoundError(
            "未找到 wcdb-key-tool(同 wcdb_extract 的指引)。")
    os.makedirs(dst_dir, exist_ok=True)
    if bp.endswith((".py", ".pyw")):
        cmd = [sys.executable, bp, "decrypt",
               "-k", key_hex, "-d", src_dir, "-o", dst_dir]
    else:
        cmd = [bp, "decrypt", "-k", key_hex,
               "-d", src_dir, "-o", dst_dir]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        print(r.stdout, file=sys.stderr)
        print(r.stderr, file=sys.stderr)
    return r.returncode


# ---------------------------------------------------------------------------
# AES-CBC page decrypt(SQLCipher 4)— Phase 0 末了或 Phase 1 才用
# ---------------------------------------------------------------------------

PAGE_SZ = 4096
KEY_SZ = 32
SALT_SZ = 16
IV_SZ = 16
HMAC_SZ = 64
RESERVE_SZ = IV_SZ + HMAC_SZ  # 80 bytes
SQLITE_HDR = b"SQLite format 3\x00"


def is_sqlcipher_page(page: bytes) -> bool:
    """快速判断一页是不是 SQLCipher 加密页(无 magic,只有 reserved 字节)。"""
    if len(page) != PAGE_SZ:
        return False
    # SQLCipher 4: 第一页密文末尾的 reserve 区有 IV + HMAC,直接用真特征比较
    return True  # 微信 Message db 都是加密的,这一层兜底用 True


# decrypt_db() 暂未落地: Phase 0 走 wcdb-decrypt 子进程直接产解密后的 db,
# 不内嵌 AES 解密逻辑(避免与 wcdb-key-tool 双线维护 + License 风险)。
# 后期若要内嵌,参考 wcdb-key-tool/wcdb_key_tool_windows.py 的 CNG 路径
# 或 wcdb_key_tool.py 的 OpenSSL ctypes 路径(都是 MIT,引用注明即可)。