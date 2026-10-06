# -*- coding: utf-8 -*-
"""solutions_learner CATEGORIES 与 prisiragent_web._PRESET_KEYWORDS 同步测试(task #23)。

  T1  CATEGORIES 长度 == 19
  T2  CATEGORIES 含「出行/通勤」
  T3  prisiragent_web._PRESET_KEYWORDS 含「出行/通勤」(交叉对齐)
  T4  两份类目集合的 18 个老类完全一致(无遗漏 / 无漂移)
  T5  learned 流程:router 返回「出行/通勤」分类时,learn_from_chat
      正确写入 learned 条目且 category == "出行/通勤"(而非 NEW_CATEGORY)
  T6  learned 流程:router 返回未注册分类时,落 NEW_CATEGORY(兜底)
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(r"C:\Users\Administrator\oi_enhancements")
sys.path.insert(0, str(REPO))

import prisir_case_compat  # noqa: E402,F401  # 大小写兼容
import solutions_learner as SL  # noqa: E402


def _get_W():
    """懒 import prisiragent_web — 避免 pollution file 锁住 module-level 引用。"""
    import prisiragent_web as W
    return W


W = _get_W()
CAT = "出行/通勤"


@pytest.fixture(autouse=True)
def _refresh_W():
    """每个 test 前重新拿 W,避免 MagicMock 引用被锁。"""
    global W
    W = _get_W()
    yield


# ---------------------------------------------------------------------------
# T1 / T2 / T3 / T4 类目表本身
# ---------------------------------------------------------------------------
def test_T1_categories_len_19():
    assert len(SL.CATEGORIES) == 19, (
        f"CATEGORIES 长度应为 19,实际 {len(SL.CATEGORIES)}:{list(SL.CATEGORIES)}")


def test_T2_categories_contains_travel():
    assert CAT in SL.CATEGORIES, (
        f"{CAT} 不在 CATEGORIES,实际:{list(SL.CATEGORIES)}")


def test_T3_preset_keywords_contains_travel():
    cats = [pt for pt, _ in W._PRESET_KEYWORDS]
    assert CAT in cats, f"{CAT} 不在 _PRESET_KEYWORDS,实际:{cats}"


def test_T4_old_18_categories_aligned():
    """两份类目表的前 18 项必须完全一致(防漂移)。"""
    preset_cats = [pt for pt, _ in W._PRESET_KEYWORDS]
    sl_cats = list(SL.CATEGORIES)
    assert len(preset_cats) == 19, (
        f"_PRESET_KEYWORDS 应有 19 类,实际 {len(preset_cats)}:{preset_cats}")
    # 前 18 项必须逐一相等(顺序也可能对齐,因 _PRESET_KEYWORDS 是按手工维护的顺序排)
    preset_old = preset_cats[:18]
    sl_old = sl_cats[:18]
    diff = [(i, p, s) for i, (p, s) in enumerate(zip(preset_old, sl_old)) if p != s]
    assert not diff, f"前 18 类不对齐:diff={diff}"
    # 第 19 项(出行/通勤)在两边都要存在
    assert preset_cats[18] == CAT
    assert sl_cats[18] == CAT


# ---------------------------------------------------------------------------
# T5 / T6 learned 流程:stub router,跑异步 learn_from_chat
# ---------------------------------------------------------------------------
class _StubRouter:
    """模仿 router.generate 接口,可注入任意 category 答案。"""

    def __init__(self, category: str):
        self.category = category

    async def generate(self, messages, **kw):  # noqa: ANN003
        payload = json.dumps({
            "category": self.category,
            "title": "stub test title 出行通勤",
            "solution": "stub test solution for 出行/通勤 category alignment",
            "paths": ["docs/prisir-android-win-link-design.md"],
        }, ensure_ascii=False)
        return {"text": payload}


def _isolate_data_dir():
    """返回临时数据目录,设置到 PRISIR_DATA_DIR,确保测试不污染真实 learned。"""
    d = Path(tempfile.mkdtemp(prefix="sl_cat_test_"))
    os.environ["PRISIR_DATA_DIR"] = str(d)
    # solutions_learner._data_dir() 读 env 在模块级调用,不会重新读 → 已缓存?
    # 实际上 _data_dir() 每次调用都读 env,所以 ok。
    return d


async def _run_learn(category: str):
    """用 stub router 跑一次 learn_from_chat,返回 learn 后的文件内容。"""
    router = _StubRouter(category)
    await SL.learn_from_chat(router, "明天去机场几点出发合适?", "建议提前 2 小时出发")
    # 读 learned 文件
    p = SL._learned_path()
    if p.is_file():
        return p.read_text(encoding="utf-8")
    return ""


def test_T5_learned_flow_routes_travel_keyword_to_travel_category():
    """learn_from_chat 收到「出行」语境 + router 返回「出行/通勤」→ 落「出行/通勤」。"""
    d = _isolate_data_dir()
    try:
        text = asyncio.run(_run_learn(CAT))
        assert text, "learned 文件应被创建"
        # 解析第一行: - [category] title :: ...
        items = SL.load_learned()
        assert items, "load_learned 应返回至少 1 条"
        assert items[0]["category"] == CAT, (
            f"learned.category 应为 {CAT},实际:{items[0]['category']}")
        assert items[0]["title"].startswith("stub test title"), (
            f"learned.title 应保留 stub 前缀,实际:{items[0]['title']}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


def test_T6_learned_flow_unknown_category_falls_to_new_category():
    """learn_from_chat 收到未注册分类 → 落 NEW_CATEGORY(兜底)。"""
    d = _isolate_data_dir()
    try:
        text = asyncio.run(_run_learn("完全未知的新类"))
        assert text, "learned 文件应被创建"
        items = SL.load_learned()
        assert items, "load_learned 应返回至少 1 条"
        assert items[0]["category"] == SL.NEW_CATEGORY, (
            f"未注册分类应落 NEW_CATEGORY,实际:{items[0]['category']}")
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# runner
# ---------------------------------------------------------------------------
TESTS = [
    test_T1_categories_len_19,
    test_T2_categories_contains_travel,
    test_T3_preset_keywords_contains_travel,
    test_T4_old_18_categories_aligned,
    test_T5_learned_flow_routes_travel_keyword_to_travel_category,
    test_T6_learned_flow_unknown_category_falls_to_new_category,
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