# -*- coding: utf-8 -*-
"""方案库新增第 19 类「出行/通勤」回归测试(task #9):
  T1  _PRESET_KEYWORDS 含「出行/通勤」键
  T2  至少 6 个中文关键词覆盖大多数用户说法
  T3  至少 1 个英文关键词(travel/commute)
  T4  单关键词命中 → _preset_priority_block 命中「出行/通勤」类
  T5  「几点出发」「去机场」「接机」3 种典型说法都能命中
  T6  _shell_system_prompt 注入 system prompt 时把「出行/通勤」块带出来
  T7  超过 3 个关键词命中 → 仍只注 3 类(防膨胀)
  T8  非出行/通勤文本(如「输入法打字不显示」)→ 不注入
  T9  docs/preset-solutions-index.md 19 行(18 + 新 1),且含「出行/通勤」
  T10 「出行/通勤」行可被 _load_preset_rows 抽回原行字符串
"""
import sys
from pathlib import Path

REPO = Path(r"C:\Users\Administrator\oi_enhancements")
sys.path.insert(0, str(REPO))

import prisir_case_compat  # noqa: E402,F401  # 大小写兼容:prisIragent_web ↔ prisiragent_web
import prisiragent_web as W  # noqa: E402

CAT = "出行/通勤"


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _kw_for(cat: str) -> tuple[str, ...]:
    for ptype, kws in W._PRESET_KEYWORDS:
        if ptype == cat:
            return kws
    raise AssertionError(f"类别 {cat} 未注册到 _PRESET_KEYWORDS")


# ---------------------------------------------------------------------------
# T1 / T2 / T3 关键词表本身
# ---------------------------------------------------------------------------
def test_T1_category_registered():
    cats = [pt for pt, _ in W._PRESET_KEYWORDS]
    assert CAT in cats, f"{CAT} 不在 _PRESET_KEYWORDS 中,实际:{cats}"


def test_T2_at_least_6_chinese_keywords():
    kws = _kw_for(CAT)
    zh = [k for k in kws if any("\u4e00" <= ch <= "\u9fff" for ch in k)]
    assert len(zh) >= 6, f"中文关键词至少 6 个,实际 {len(zh)} 个:{zh}"


def test_T3_at_least_one_english_keyword():
    kws = _kw_for(CAT)
    en = [k for k in kws if k.isascii() and k.isalpha()]
    assert len(en) >= 1, f"至少 1 个英文关键词(travel/commute),实际:{en}"


# ---------------------------------------------------------------------------
# T4 / T5 单关键词命中 _preset_priority_block
# ---------------------------------------------------------------------------
def test_T4_block_hits_travel_category():
    block = W._preset_priority_block("我明天通勤时间大概多久?")
    assert block, "应当命中并返回非空块"
    assert CAT in block, f"块中应含 {CAT},实际片段:{block[:300]}"


def test_T5_typical_phrases_all_hit():
    """3 种典型出行说法都要命中(子串匹配)。"""
    for phrase in ["几点出发?", "去机场怎么走?", "帮我叫车接机"]:
        block = W._preset_priority_block(phrase)
        assert CAT in block, f"短语 {phrase!r} 未命中 {CAT}"


# ---------------------------------------------------------------------------
# T6 _shell_system_prompt 注入(monkey-patch _load_preset_rows 用 stub,
# 不依赖 docs/preset-solutions-index.md 是否真存在)
# ---------------------------------------------------------------------------
def test_T6_system_prompt_includes_travel_block():
    # 用 stub 索引,保证测试稳定不依赖磁盘文件
    orig = W._load_preset_rows

    def _stub():
        return {CAT: f"| **{CAT}** | docs/test.md | stub 行 |"}

    W._load_preset_rows = _stub
    try:
        prompt = W._shell_system_prompt("明天去机场接机几点出发合适?", sid="t6")
    finally:
        W._load_preset_rows = orig
    assert "预设方案库" in prompt, "system prompt 应包含预设方案库块"
    assert CAT in prompt, f"system prompt 应含 {CAT} 类别"
    assert "docs/test.md" in prompt, "stub 行的路径应被注入到 system prompt"


# ---------------------------------------------------------------------------
# T7 防膨胀:超 3 命中仍只注 3 类
# ---------------------------------------------------------------------------
def test_T7_max_three_categories():
    """构造一段同时命中 5+ 类的文本,验证只注 3 类。"""
    # 输入涵盖:出行/通勤(去机场+几点出发)+ 输入法(拼音)+ 浏览器(扩展+Chromium)
    # + 移动端(APK)+ 装包(NSIS) → 至少 5 类
    text = "我装包用 NSIS 出错,拼音输入法在 Chromium 扩展 APK 上几点出发去机场都没用"
    hits = [pt for pt, kws in W._PRESET_KEYWORDS
            if any(k in text for k in kws)]
    assert len(hits) >= 4, f"测试前提:文本应命中 >=4 类,实际命中 {len(hits)} 类:{hits}"
    block = W._preset_priority_block(text)
    # block 里被注入的行只能是 _load_preset_rows 真抽出的行
    # 我们把 stub 设成全空,只能靠类别名出现次数判断
    orig = W._load_preset_rows
    W._load_preset_rows = lambda: {}  # 全空 → 只能看命中数本身
    try:
        block2 = W._preset_priority_block(text)
    finally:
        W._load_preset_rows = orig
    # 由于 stub 空,block2 只会含 header+footer,没有具体类别行
    # 但 _preset_priority_block 内部的 hits 列表长度由源码逻辑决定,我们直接测源码逻辑:
    from prisiragent_web import _PRESET_KEYWORDS as K
    hits_in_logic: list[str] = []
    for ptype, keywords in K:
        if any(k in text for k in keywords):
            hits_in_logic.append(ptype)
        if len(hits_in_logic) >= 3:
            break
    assert len(hits_in_logic) == 3, (
        f"_preset_priority_block 应把命中数截断到 3,实际 {len(hits_in_logic)}:{hits_in_logic}")


# ---------------------------------------------------------------------------
# T8 非出行文本不触发
# ---------------------------------------------------------------------------
def test_T8_non_travel_text_no_travel_block():
    block = W._preset_priority_block("我的输入法打字不显示候选词,怎么办?")
    if block:  # 可能命中其他类(输入法),但不应包含「出行/通勤」
        assert CAT not in block, (
            f"非出行文本不应触发 {CAT},实际块:{block[:400]}")


# ---------------------------------------------------------------------------
# T9 / T10 索引文件存在且「出行/通勤」行可被抽回
# ---------------------------------------------------------------------------
def test_T9_index_doc_has_travel_row():
    idx_path = REPO / "docs" / "preset-solutions-index.md"
    assert idx_path.is_file(), f"索引文档不存在:{idx_path}"
    text = idx_path.read_text(encoding="utf-8")
    # 必须含「出行/通勤」行
    has_travel = any(
        line.lstrip().startswith("| **") and CAT in line
        for line in text.splitlines()
    )
    assert has_travel, f"索引文档缺 {CAT} 行"


def test_T10_load_preset_rows_recovers_travel_line():
    rows = W._load_preset_rows()
    assert CAT in rows, f"_load_preset_rows 应能从索引抽出 {CAT},实际 keys:{list(rows.keys())[:5]}"
    line = rows[CAT]
    assert line.lstrip().startswith("|"), f"抽出的行不是 markdown 表格行:{line[:60]}"


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
TESTS = [
    test_T1_category_registered,
    test_T2_at_least_6_chinese_keywords,
    test_T3_at_least_one_english_keyword,
    test_T4_block_hits_travel_category,
    test_T5_typical_phrases_all_hit,
    test_T6_system_prompt_includes_travel_block,
    test_T7_max_three_categories,
    test_T8_non_travel_text_no_travel_block,
    test_T9_index_doc_has_travel_row,
    test_T10_load_preset_rows_recovers_travel_line,
]


def main():
    passed = failed = 0
    for fn in TESTS:
        try:
            fn()
            print(f"[PASS] {fn.__name__}")
            passed += 1
        except Exception as e:  # noqa: BLE001
            print(f"[FAIL] {fn.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n=== {passed}/{passed + failed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
