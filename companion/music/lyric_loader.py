# -*- coding: utf-8 -*-
"""
lyric_loader.py — M3.28 Path B LRC 解析器
解析 LX /lyric LRC 文本 → [{time_ms, text, words?}]
配合 SSE lyricLineText 实时高亮当前行。

支持的 LRC 行格式:
  [mm:ss.xx] 文本
  [mm:ss.xxx] 文本       (3 位毫秒)
  [hh:mm:ss.xx] 文本      (罕见但兼容)
  [mm:ss.xx]<mm:ss.xx> 文本  (多时间标签,LX 罕见)
跳过 metadata 行:[ar:][ti:][by:][hash:][al:][sign:][qq:][total:][offset:]
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

# 单行内的多时间标签:[00:01.00][00:30.50]文本
_TIME_TAG_RE = re.compile(r"\[(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?\]")
# 逐字时间标签:<00:00.00>字<00:00.50>字 (卡拉OK / Karacookie / Apple Music Big Lyrics)
_WORD_TAG_RE = re.compile(r"<(\d{1,3}):(\d{1,2})(?:[.:](\d{1,3}))?>")


@dataclass
class LyricLine:
    time_ms: int                 # 该行起始时间(毫秒)
    text: str                    # 文本(已剥 metadata / 时间标签)
    words: List["LyricWord"] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"LyricLine({self.time_ms}ms, {len(self.words)}w, {self.text!r})"


@dataclass
class LyricWord:
    time_ms: int
    text: str


def _to_ms(m: int, s: int, frac: Optional[str]) -> int:
    """mm:ss.xxx → 毫秒;frac 可能是 2 位(厘秒)或 3 位(毫秒)"""
    if not frac:
        ms = 0
    elif len(frac) == 2:
        ms = int(frac) * 10
    elif len(frac) == 3:
        ms = int(frac)
    else:
        # 兜底按厘秒处理
        try:
            ms = int(frac[:3].ljust(3, "0"))
        except ValueError:
            ms = 0
    return (m * 60 + s) * 1000 + ms


def parse_lrc(text: str) -> List[LyricLine]:
    """解析 LRC → 行数组(按 time_ms 升序)"""
    if not text:
        return []
    # 按行解析;行可能有多个 [time] 标签(共享同一文本)
    # 也可能有 <word_time> 字
    lines: List[LyricLine] = []
    for raw in text.splitlines():
        line = raw.rstrip("\r\n")
        if not line.strip():
            continue
        # 提取行内所有时间标签
        tags = _TIME_TAG_RE.findall(line)
        # 提取逐字标签(可选)
        word_tags = _WORD_TAG_RE.findall(line)
        if not tags and not word_tags:
            # metadata 行([ar:...][ti:...])或纯文本,跳过
            if line.lstrip().startswith("["):
                continue
            # 纯文本无时间 → 用 0 占位
            lines.append(LyricLine(0, line.strip()))
            continue
        # 剥所有 [time] 标签 + <word> 标签,留纯文本
        clean = _TIME_TAG_RE.sub("", line)
        clean = _WORD_TAG_RE.sub("", clean).strip()
        # 处理逐字
        words: List[LyricWord] = []
        if word_tags:
            # 把 <time> 字 拆成逐字(简化:按标签 split 后插入)
            # 由于 _WORD_TAG_RE.sub 已剥,这里用 re.finditer 拿原文切片
            cursor = 0
            last_t = None
            for m in _WORD_TAG_RE.finditer(line):
                w = m.group(0)  # "<00:01.00>"
                wm = m.group(1)
                ws = m.group(2)
                wf = m.group(3)
                t_ms = _to_ms(int(wm), int(ws), wf)
                # 字文本 = 上一个 word 标签后到此 word 标签前
                if last_t is not None:
                    txt = line[last_t[1]:m.start()].strip()
                    if txt:
                        words.append(LyricWord(last_t[0], txt))
                last_t = (t_ms, m.end())
                cursor = m.end()
            # 最后一段
            if last_t is not None:
                txt = line[last_t[1]:].strip()
                if txt:
                    words.append(LyricWord(last_t[0], txt))
        for mm, ss, ff in tags:
            t_ms = _to_ms(int(mm), int(ss), ff)
            lines.append(LyricLine(time_ms=t_ms, text=clean, words=words[:]))
    # 按时间升序
    lines.sort(key=lambda l: l.time_ms)
    return lines


def find_line_at(lines: List[LyricLine], progress_ms: int) -> int:
    """返回 progress_ms 时刻应高亮的行 index;无匹配返 -1。"""
    if not lines:
        return -1
    # 二分:最后一行 time_ms <= progress_ms
    lo, hi = 0, len(lines) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if lines[mid].time_ms <= progress_ms:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def find_word_at(line: LyricLine, progress_ms: int) -> int:
    """逐字高亮 index。"""
    if not line.words:
        return -1
    lo, hi = 0, len(line.words) - 1
    ans = -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if line.words[mid].time_ms <= progress_ms:
            ans = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return ans


def quick_smoke() -> dict:
    """独立 smoke 测试"""
    sample = """[ti:June]
[ar:arkady sevidov]
[00:01.589]纯音乐,请欣赏
[00:03.000]第一行
[00:06.500]第二行
[00:10.000]第三行"""
    lines = parse_lrc(sample)
    return {
        "line_count": len(lines),
        "first": repr(lines[0]) if lines else None,
        "at_2500ms": find_line_at(lines, 2500),  # 0: 1589ms 行
        "at_7000ms": find_line_at(lines, 7000),  # 2: 6500ms 行(下一行 10000 还没到)
    }


if __name__ == "__main__":
    import json
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))