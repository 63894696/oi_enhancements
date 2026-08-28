# -*- coding: utf-8 -*-
"""应用层分词:CJK unigram + 相邻 CJK bigram + ASCII 词。

思路抄 prisir_findex/src/index.rs 的 tokenize(已在 Rust 侧验证,
见 [[prisir-findex-engine]]):FTS5 的 unicode61/trigram 都不切 CJK 单字,
纯中文子串搜不到;必须建库与查询用同一套应用层分词。

- ASCII 词:连续 [A-Za-z0-9_]+ 整段作一个 token(小写化)。
- CJK unigram:每个汉字单字一个 token。
- CJK bigram:相邻两个汉字再拼一个 token(提高短语精度)。
- 其余(标点/空白/符号)一律作分隔符。

tokenize(text) 与 to_match(query) 必须同源,否则建库/查询分词不一致搜不到。
"""
import re

_CJK = r"㐀-䶿一-鿿豈-﫿"
_CJK_RE = re.compile(f"[{_CJK}]")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9_]+")


def _is_cjk(ch: str) -> bool:
    return bool(_CJK_RE.match(ch))


def tokenize(text: str):
    """把任意文本切成 token 列表(建库侧用)。"""
    tokens = []
    ascii_buf = []
    prev_cjk = None  # 上一个 CJK 字,用于拼 bigram

    def flush_ascii():
        if ascii_buf:
            tokens.append("".join(ascii_buf).lower())
            ascii_buf.clear()

    for ch in text:
        if _ASCII_WORD_RE.match(ch):
            # ASCII 词字符:累积,且打断 CJK bigram 链
            ascii_buf.append(ch)
            prev_cjk = None
        elif _is_cjk(ch):
            flush_ascii()
            tokens.append(ch)  # unigram
            if prev_cjk is not None:
                tokens.append(prev_cjk + ch)  # bigram
            prev_cjk = ch
        else:
            # 分隔符
            flush_ascii()
            prev_cjk = None
    flush_ascii()
    return tokens


def to_match(query: str):
    """把查询串转成 FTS5 MATCH 表达式(查询侧用,与 tokenize 同源)。

    - CJK 连续段:bigram 优先(相邻字拼 bigram AND),段长 1 时退 unigram。
    - ASCII 词:整词 AND。
    - 各 token 之间 AND(全部命中)。
    返回 None 表示空查询(调用方退化为 mtime 全扫)。
    """
    q = (query or "").strip()
    if not q:
        return None
    terms = []
    ascii_buf = []
    cjk_run = []

    def flush_ascii():
        if ascii_buf:
            w = "".join(ascii_buf).lower()
            # 转义双引号(FTS5 短语语法)
            terms.append('"' + w.replace('"', '""') + '"')
            ascii_buf.clear()

    def flush_cjk():
        if not cjk_run:
            return
        if len(cjk_run) == 1:
            terms.append('"' + cjk_run[0] + '"')
        else:
            for i in range(len(cjk_run) - 1):
                bg = cjk_run[i] + cjk_run[i + 1]
                terms.append('"' + bg + '"')
        cjk_run.clear()

    for ch in q:
        if _ASCII_WORD_RE.match(ch):
            flush_cjk()
            ascii_buf.append(ch)
        elif _is_cjk(ch):
            flush_ascii()
            cjk_run.append(ch)
        else:
            flush_ascii()
            flush_cjk()
    flush_ascii()
    flush_cjk()
    if not terms:
        return None
    return " AND ".join(terms)
