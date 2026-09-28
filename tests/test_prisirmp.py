# -*- coding: utf-8 -*-
"""tests/test_prisirmp.py — prisirmp 单元测试。

覆盖(全部必绿,无 skip):
  · test_parse_xml_basic           — 正常公众号文章 XML 解析
  · test_parse_xml_cdata           — CDATA 包裹
  · test_parse_xml_non_article     — type!=5 应返 None
  · test_parse_xml_truncated       — 坏 XML 走 regex 兜底
  · test_url_normalize             — 同 __biz/mid/idx/sn 不同追踪 → 同 url
  · test_tokenize_consistency      — tokenize 与 to_match 同源(中文/英文)
  · test_engine_upsert_search      — 入库 → 搜索命中
  · test_engine_clear_rebuild      — 清空后能重建
  · test_engine_persistence        — 重启后 last_index 不丢
  · test_engine_accounts           — accounts 增删
  · test_cli_status_help           — CLI --help 和 status 都跑得起来
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# parse_appmsg_xml
# ---------------------------------------------------------------------------


def test_parse_xml_basic():
    from prisirmp.extract import parse_appmsg_xml
    xml = (
        '<msg><appmsg><type>5</type>'
        '<title>深度学习入门</title>'
        '<url>https://mp.weixin.qq.com/s?__biz=A&amp;mid=1&amp;idx=1&amp;sn=a</url>'
        '<item><datadesc><sourceusername>机器之心</sourceusername></datadesc></item>'
        '</appmsg></msg>'
    )
    r = parse_appmsg_xml(xml)
    assert r and "深度学习" in r["title"] and r["publisher"] == "机器之心"
    assert "__biz=A" in r["url"] and "mid=1" in r["url"]
    print("✓ parse_xml_basic")


def test_parse_xml_cdata():
    from prisirmp.extract import parse_appmsg_xml
    xml = (
        '<msg><appmsg><type>5</type>'
        '<title><![CDATA[Foo Bar]]></title>'
        '<url><![CDATA[https://mp.weixin.qq.com/s?__biz=B&amp;mid=2&amp;idx=1&amp;sn=b]]></url>'
        '</appmsg></msg>'
    )
    r = parse_appmsg_xml(xml)
    assert r and r["title"] == "Foo Bar"
    print("✓ parse_xml_cdata")


def test_parse_xml_non_article():
    from prisirmp.extract import parse_appmsg_xml
    # type=6 文件传输
    xml = '<msg><appmsg><type>6</type><title>file.zip</title><url>https://x/y</url></appmsg></msg>'
    assert parse_appmsg_xml(xml) is None
    # type=33 小程序
    xml2 = '<msg><appmsg><type>33</type><title>小程序</title><url>https://x/y</url></appmsg></msg>'
    assert parse_appmsg_xml(xml2) is None
    print("✓ parse_xml_non_article")


def test_parse_xml_truncated():
    from prisirmp.extract import parse_appmsg_xml
    # 坏 XML(右尖括号 / 缺收尾)— regex 兜底
    xml = (
        '<msg><appmsg><type>5</type>'
        '<title>坏掉</title>'
        '<url>https://mp.weixin.qq.com/s?__biz=B&amp;mid=2&amp;idx=1&amp;sn=b</url>'
        '</appmsg></TRUNCATED'
    )
    r = parse_appmsg_xml(xml)
    assert r and "坏掉" in r["title"]
    print("✓ parse_xml_truncated")


def test_url_normalize():
    from prisirmp.extract import parse_appmsg_xml
    u1 = parse_appmsg_xml(
        '<msg><appmsg><type>5</type><title>X</title>'
        '<url>https://mp.weixin.qq.com/s?__biz=M&amp;mid=1&amp;idx=1&amp;sn=xx&amp;scene=1&amp;clicktime=12345</url>'
        '</appmsg></msg>')["url"]
    u2 = parse_appmsg_xml(
        '<msg><appmsg><type>5</type><title>X</title>'
        '<url>https://mp.weixin.qq.com/s?__biz=M&amp;mid=1&amp;idx=1&amp;sn=xx&amp;scene=99&amp;clicktime=99999</url>'
        '</appmsg></msg>')["url"]
    assert u1 == u2, f"应归一化: {u1} vs {u2}"
    # 关键身份参数还在
    assert "__biz=M" in u1 and "mid=1" in u1 and "idx=1" in u1 and "sn=xx" in u1
    # 追踪类全剥
    assert "scene=" not in u1 and "clicktime=" not in u1
    print("✓ url_normalize")


# ---------------------------------------------------------------------------
# tokenize
# ---------------------------------------------------------------------------


def test_tokenize_consistency():
    from prisirmp.tokenize import tokenize, to_match
    # 中文:unigram + bigram
    toks = tokenize("深度学习")
    assert "深" in toks and "度" in toks and "学" in toks and "习" in toks
    assert "深度" in toks and "度学" in toks and "学习" in toks
    # 英文:小写化整词
    toks_en = tokenize("Python is GREAT")
    assert "python" in toks_en and "is" in toks_en and "great" in toks_en
    # 混合
    toks_mix = tokenize("深度学习 Python")
    assert "深" in toks_mix and "python" in toks_mix
    # to_match 不返 None
    m = to_match("深度学习")
    assert m and "深度" in m and "学习" in m
    # 空 query
    assert to_match("") is None
    assert to_match("   ") is None
    print("✓ tokenize_consistency")


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def test_engine_upsert_search():
    from prisirmp.engine import Mprecaller
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        Mprecaller._reset_singleton()
        rc = Mprecaller(db)
        arts = [
            {"biz": "a", "title": "深度学习入门", "url": "https://x/1",
             "desc": "神经网络", "publisher": "机器之心", "ts": 1700000000,
             "source": 1, "thumburl": "", "account_id": "acc",
             "text": "神经网络发展史"},
            {"biz": "b", "title": "Python 异步", "url": "https://x/2",
             "desc": "asyncio", "publisher": "Py 美", "ts": 1700100000,
             "source": 1, "thumburl": "", "account_id": "acc",
             "text": ""},
        ]
        assert rc.upsert_articles(arts) == 2
        res = rc.search("深度学习")
        assert res["total"] == 1 and "深度学习" in res["hits"][0]["title"]
        res2 = rc.search("Python")
        assert res2["total"] == 1
        rc.close()


def test_engine_clear_rebuild():
    from prisirmp.engine import Mprecaller
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        Mprecaller._reset_singleton()
        rc = Mprecaller(db)
        rc.upsert_articles([{"biz": "x", "title": "A", "url": "https://x/1",
                              "desc": "", "publisher": "p", "ts": 1,
                              "source": 1, "thumburl": "", "account_id": "a",
                              "text": ""}])
        assert rc.status()["indexed_count"] == 1
        rc.clear_articles()
        assert rc.status()["indexed_count"] == 0
        # 重建(应仍可搜)
        rc.upsert_articles([{"biz": "x", "title": "A", "url": "https://x/1",
                              "desc": "", "publisher": "p", "ts": 1,
                              "source": 1, "thumburl": "", "account_id": "a",
                              "text": ""}])
        assert rc.status()["indexed_count"] == 1
        rc.close()


def test_engine_persistence():
    from prisirmp.engine import Mprecaller
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        Mprecaller._reset_singleton()
        rc = Mprecaller(db)
        rc.upsert_articles([{"biz": "x", "title": "A", "url": "https://x/1",
                              "desc": "", "publisher": "p", "ts": 1,
                              "source": 1, "thumburl": "", "account_id": "a",
                              "text": ""}])
        ts1 = rc.status()["last_index"]
        assert ts1 > 0
        rc.close()

        Mprecaller._reset_singleton()
        rc2 = Mprecaller(db)
        st = rc2.status()
        assert st["indexed_count"] == 1
        assert st["last_index"] == ts1  # last_index 从 meta 表读回
        rc2.close()


def test_engine_accounts():
    from prisirmp.engine import Mprecaller
    with tempfile.TemporaryDirectory() as tmp:
        db = os.path.join(tmp, "t.db")
        Mprecaller._reset_singleton()
        rc = Mprecaller(db)
        rc.upsert_account("a1", "/src/1", "/dst/1", 100)
        rc.upsert_account("a2", "/src/2", "/dst/2", 200)
        st = rc.status()
        assert st["account_count"] == 2
        assert {a["message_count"] for a in st["accounts"]} == {100, 200}
        rc.close()


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_status_help():
    """CLI --help / status 都跑得起来;真实 wx 数据库不在时仍优雅提示。"""
    r1 = subprocess.run([sys.executable, "-m", "prisirmp", "--help"],
                        capture_output=True, text=True, cwd=REPO_ROOT)
    assert r1.returncode == 0 and "usage: prisirmp" in r1.stdout, r1.stdout
    r2 = subprocess.run([sys.executable, "-m", "prisirmp", "status"],
                        capture_output=True, text=True, cwd=REPO_ROOT)
    # status 应 return 0(显示未启用也算正常)
    assert r2.returncode == 0
    print("✓ cli --help + status")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    import pytest as _pytest
    return _pytest.main([__file__, "-v"])


if __name__ == "__main__":
    sys.exit(main())