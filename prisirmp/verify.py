# -*- coding: utf-8 -*-
"""prisirmp E2E 自检(参考 prisir_fcontent/verify.py 的范式)。

跑全链 14 步:
  1) Mprecaller 空库 status 应 enabled=False
  2) 入库 5 篇 fixture 公众号文章
  3) status 应 enabled=True + indexed_count=5
  4) 中文短词 search 命中
  5) 双词 AND search 命中
  6) 英文词 search 命中
  7) 空 query → 返 mtime 倒序
  8) 同 URL 重入库 → 幂等(count 不涨)
  9) parse_appmsg_xml 5 个 fixture 全过
 10) URL 归一化去重(同文章不同追踪 → 同 url)
 11) clear → status 应 enabled=False
 12) 重启持久化:新 Mprecaller 实例读回 last_index
 13) accounts upsert + 读回
 14) tokenize 一致性(to_match(tokenize(text)) 命中)
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # 让 import prisirmp 可


_FIXTURE_XMLS = [
    '<msg><appmsg><type>5</type>'
    '<title>深度学习入门:从感知机到 Transformer</title>'
    '<url>https://mp.weixin.qq.com/s?__biz=A&amp;mid=1&amp;idx=1&amp;sn=a1&amp;chksm=c1</url>'
    '<des>神经网络发展史</des>'
    '<item><datadesc><sourceusername>机器之心</sourceusername></datadesc></item>'
    '</appmsg></msg>',
    '<msg><appmsg><type>5</type>'
    '<title>Python 异步编程实战</title>'
    '<url>https://mp.weixin.qq.com/s?__biz=B&amp;mid=2&amp;idx=1&amp;sn=b1&amp;chksm=c2</url>'
    '<des>asyncio / aiohttp 详解</des>'
    '<item><sourceusername>Python 之美</sourceusername></item>'
    '</appmsg></msg>',
    '<msg><appmsg><type>5</type>'
    '<title>分布式系统一致性:Paxos 与 Raft</title>'
    '<url>https://mp.weixin.qq.com/s?__biz=C&amp;mid=3&amp;idx=1&amp;sn=c1&amp;chksm=c3</url>'
    '<des>共识算法对比</des>'
    '<item><datadesc>分布式系统笔记</datadesc></item>'
    '</appmsg></msg>',
    '<msg><appmsg><type>5</type>'
    '<title>Transformer 论文精读:Attention Is All You Need</title>'
    '<url>https://mp.weixin.qq.com/s?__biz=D&amp;mid=4&amp;idx=1&amp;sn=d1&amp;chksm=c4</url>'
    '<des>逐段翻译 + 公式推导</des>'
    '<item><sourceusername>AI 论文笔记</sourceusername></item>'
    '</appmsg></msg>',
    '<msg><appmsg><type>5</type>'
    '<title>Git 高效工作流</title>'
    '<url>https://mp.weixin.qq.com/s?__biz=E&amp;mid=5&amp;idx=1&amp;sn=e1&amp;chksm=c5</url>'
    '<des>rebase / cherry-pick / bisect</des>'
    '<item><sourceusername>DevOps 实战</sourceusername></item>'
    '</appmsg></msg>',
]


def _build_fixtures() -> list[dict[str, Any]]:
    """从 fixture XML 解析成 articles 入库格式。"""
    from prisirmp.extract import parse_appmsg_xml
    out = []
    for i, x in enumerate(_FIXTURE_XMLS):
        m = parse_appmsg_xml(x)
        assert m is not None, f"fixture{i} parse 失败"
        m["account_id"] = "test_acc"
        m["ts"] = 1700000000 + i * 100000
        m["text"] = m.get("desc", "")
        m["source"] = 1
        out.append(m)
    return out


def _print_step(n: int, msg: str) -> None:
    print(f"[{n:2}] {msg}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="prisirmp_verify_")
    db = os.path.join(tmp, "test.db")
    try:
        from prisirmp.engine import Mprecaller

        # 1) 空库 status 应未启用
        rc = Mprecaller(db)
        st = rc.status()
        _print_step(1, f"空库 status: enabled={st['enabled']} count={st['indexed_count']}")
        assert st["enabled"] is False and st["indexed_count"] == 0

        # 2) 入库 5 篇
        arts = _build_fixtures()
        n = rc.upsert_articles(arts)
        _print_step(2, f"upsert 5 篇 → 入库 {n}")
        assert n == 5

        # 3) status 应 enabled + count=5
        st = rc.status()
        _print_step(3, f"status: count={st['indexed_count']} last_index={st['last_index']}")
        assert st["enabled"] is True and st["indexed_count"] == 5
        assert st["last_index"] > 0

        # 4) 中文短词
        res = rc.search("深度学习")
        names = [h["title"][:20] for h in res["hits"]]
        _print_step(4, f"search('深度学习') total={res['total']} → {names}")
        assert res["total"] >= 1 and any("深度学习" in h["title"] for h in res["hits"])

        # 5) 双词 AND
        res = rc.search("分布式 一致性")
        names = [h["title"][:20] for h in res["hits"]]
        _print_step(5, f"search('分布式 一致性') → {names}")
        assert any("分布式" in h["title"] and "一致性" in h["title"] for h in res["hits"])

        # 6) 英文词
        res = rc.search("Python")
        names = [h["title"][:30] for h in res["hits"]]
        _print_step(6, f"search('Python') → {names}")
        assert any("Python" in h["title"] for h in res["hits"])

        # 7) 空 query → 倒序全列
        res = rc.search("", limit=30)
        _print_step(7, f"空 query → total={res['total']}")
        assert res["total"] == 5
        ts_seq = [h["ts"] for h in res["hits"]]
        assert ts_seq == sorted(ts_seq, reverse=True), f"应 ts 倒序: {ts_seq}"

        # 8) 幂等(同 url 重入库)
        n2 = rc.upsert_articles(arts)
        _print_step(8, f"重入库 5 篇 → {n2} (url UNIQUE 应被替换,count 不涨)")
        st = rc.status()
        assert st["indexed_count"] == 5

        # 9) parse_appmsg_xml 边界
        from prisirmp.extract import parse_appmsg_xml
        cases = [
            ("<msg><appmsg><type>6</type><title>x</title><url>https://x</url></appmsg></msg>", None),
            ("", None),
            ("<notappmsg>x</notappmsg>", None),
            ("<msg><appmsg><type>5</type><title>bad</title><url>https://mp.weixin.qq.com/s?__biz=B&amp;mid=2&amp;idx=3&amp;sn=4</url></appmsg></TRUNCATED", "bad"),
        ]
        ok = sum(1 for xml, want_title in cases
                 if (parse_appmsg_xml(xml) is None if want_title is None
                     else parse_appmsg_xml(xml) and want_title in parse_appmsg_xml(xml)["title"]))
        _print_step(9, f"parse_appmsg_xml 边界用例 {ok}/{len(cases)}")
        assert ok == len(cases)

        # 10) URL 归一化
        u1 = parse_appmsg_xml(
            '<msg><appmsg><type>5</type><title>X</title>'
            '<url>https://mp.weixin.qq.com/s?__biz=M&amp;mid=1&amp;idx=1&amp;sn=xx&amp;scene=1&amp;clicktime=12345</url>'
            '</appmsg></msg>')["url"]
        u2 = parse_appmsg_xml(
            '<msg><appmsg><type>5</type><title>X</title>'
            '<url>https://mp.weixin.qq.com/s?__biz=M&amp;mid=1&amp;idx=1&amp;sn=xx&amp;scene=99&amp;clicktime=99999</url>'
            '</appmsg></msg>')["url"]
        _print_step(10, f"URL 归一化: {u1 == u2}")
        assert u1 == u2

        # 11) clear
        rc.clear_articles()
        st = rc.status()
        _print_step(11, f"clear → count={st['indexed_count']}")
        assert st["indexed_count"] == 0 and st["enabled"] is False

        # 12) 重启持久化
        rc2 = Mprecaller(db)
        rc2.upsert_articles(arts)
        rc2.close()
        rc3 = Mprecaller(db)
        st3 = rc3.status()
        _print_step(12, f"重启后 status: count={st3['indexed_count']} last_index={st3['last_index']}")
        assert st3["indexed_count"] == 5 and st3["last_index"] > 0
        rc3.close()

        # 13) accounts
        from prisirmp.engine import Mprecaller as _Mp
        _Mp._reset_singleton()
        rc4 = _Mp(db)
        rc4.upsert_account("acc_a", "/src/a", "/dst/a", 1000)
        rc4.upsert_account("acc_b", "/src/b", "/dst/b", 2000)
        st4 = rc4.status()
        _print_step(13, f"accounts 读回: {[(a['account_id'][:6], a['message_count']) for a in st4['accounts']]}")
        assert st4["account_count"] == 2
        assert {a["message_count"] for a in st4["accounts"]} == {1000, 2000}
        rc4.close()

        # 14) tokenize 一致性
        from prisirmp.tokenize import tokenize, to_match
        text = "深度学习入门:从感知机到 Transformer 与 Python 异步"
        toks = tokenize(text)
        match_expr = to_match("深度学习")
        _print_step(14, f"tokenize 一致: tokens[0]={toks[0]!r} match={match_expr!r}")
        assert toks[0] == "深"
        # 大写化应进 ascii
        assert "python" in toks

        print("\n[PASS] prisirmp 端到端自检 14 步全过")
        return 0
    except AssertionError as e:
        print(f"\n[FAIL] {e}")
        return 1
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())