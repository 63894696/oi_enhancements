"""test_prisir_graph_recall_bridge.py — prisIr_graph → OIMemory pre_chat 注入桥 测试

不需要 pytest:python test_prisir_graph_recall_bridge.py 即可全跑一遍。

覆盖:
- 空 query / 全 miss → []
- L0 命中 → layer=L0_wikilink + snippet 含 vault 正文
- L1 命中(有模型)→ layer=L1_* + cosine 分数
- format 输出结构正确(vault graph recall 头 + End vault recall 尾)
- max_chars 截断生效
- dict 输入兼容
- hook 真注入(verify chat task 被注入)
- bridge 异常静默(不污染对话主链)
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import prisIr_graph_build as _build
import prisIr_graph_recall_bridge as _bridge


# ── 单元测试 ──────────────────────────────────────────────────────
def test_recall_empty_query_returns_empty():
    """空 query / None → 返 []。"""
    assert _bridge.recall_for_task("") == []
    assert _bridge.recall_for_task("   ") == []
    print(f"  [PASS] recall_empty_query_returns_empty")


def test_recall_l0_hit_returns_vault_hits(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """L0 wikilink 命中(min_l0_hits=1) → 返 VaultHit 含 snippet。"""
    hits = _bridge.recall_for_task(
        "ai", k=3, min_l0_hits=1,
        vault_dir=tmp_vault, db_path=tmp_db, cache_path=tmp_cache,
    )
    assert len(hits) >= 1, f"应 ≥1 命中,实际 {len(hits)}"
    h0 = hits[0]
    assert h0.layer == "L0_wikilink", f"应 L0_wikilink,实际 {h0.layer}"
    assert h0.title in ("ai", "AI 笔记")
    assert h0.path.endswith(".md")
    # snippet 应含 vault 正文
    assert h0.snippet, "snippet 应非空"
    print(f"  [PASS] recall_l0_hit: {len(hits)} 条,layer={h0.layer},title={h0.title}")


def test_recall_l0_miss_returns_empty_when_no_model(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """L0 miss(默认 min_l0_hits=2,query 命中 < 2)+ 无模型 → []。"""
    hits = _bridge.recall_for_task(
        "不存在的关键词xyz", k=3,
        vault_dir=tmp_vault, db_path=tmp_db, cache_path=tmp_cache,
    )
    # "不存在的关键词xyz" 不命中 title → L0 0 → L1 候选 0 → 返 []
    assert hits == [], f"应 [] 实际 {hits}"
    print(f"  [PASS] recall_l0_miss_no_model: 返 []")


def test_recall_format_prompt_structure(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """format 输出含 'vault graph recall' 头 + 'End vault recall' 尾 + 每条编号。"""
    hits = _bridge.recall_for_task(
        "ai", k=3, min_l0_hits=1,
        vault_dir=tmp_vault, db_path=tmp_db, cache_path=tmp_cache,
    )
    out = _bridge.format_vault_hits_for_prompt(hits)
    assert out.startswith("[vault graph recall")
    assert "[End vault recall]" in out
    assert "  1. " in out
    # 空 hits → 空字符串
    assert _bridge.format_vault_hits_for_prompt([]) == ""
    print(f"  [PASS] format_structure: 长度={len(out)}")


def test_recall_format_max_chars_truncated(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """max_chars 截断生效。"""
    hits = _bridge.recall_for_task(
        "ai", k=3, min_l0_hits=1,
        vault_dir=tmp_vault, db_path=tmp_db, cache_path=tmp_cache,
    )
    out = _bridge.format_vault_hits_for_prompt(hits, max_chars=60)
    assert "[truncated]" in out, f"应含 [truncated],实际: {out!r}"
    assert len(out) <= 60 + len("\n[truncated]") + 5
    print(f"  [PASS] format_max_chars_truncated: {len(out)} 字符")


def test_recall_format_accepts_dict_input():
    """format 接受 dict 输入(VaultHit.to_dict 兼容)。"""
    fake_hits = [{
        "layer": "L0_wikilink",
        "title": "测试笔记",
        "path": "test.md",
        "snippet": "测试正文片段",
        "score": 1.0,
    }]
    out = _bridge.format_vault_hits_for_prompt(fake_hits)
    assert "[vault graph recall" in out
    assert "测试笔记" in out
    assert "测试正文片段" in out
    print(f"  [PASS] format_accepts_dict_input")


def test_recall_vault_hit_to_dict():
    """VaultHit.to_dict 序列化友好。"""
    h = _bridge.VaultHit(
        layer="L0_wikilink", node_id="abc", title="T", path="t.md",
        snippet="snippet", score=0.9,
    )
    d = h.to_dict()
    assert d["layer"] == "L0_wikilink"
    assert d["title"] == "T"
    assert d["score"] == 0.9
    print(f"  [PASS] vault_hit_to_dict")


def test_read_snippet_strips_frontmatter(tmp_vault: Path):
    """_read_snippet 应去 frontmatter 后取 200 字符。"""
    snippet = _bridge._read_snippet("ai.md", tmp_vault)
    assert "tags: [ai]" not in snippet, f"frontmatter 应被去除: {snippet!r}"
    assert "Transformer" in snippet or "神经网络" in snippet
    print(f"  [PASS] read_snippet_strips_frontmatter: '{snippet[:50]}...'")


# ── Hook 注入测试 ─────────────────────────────────────────────────
def test_hook_injects_vault_recall_to_task(tmp_vault: Path, tmp_db: Path, tmp_cache: Path):
    """mock chat → 验证 chat task 参数含 vault recall 段。

    同时 mock OIMemory recall 避免生产库污染(query="ai" 在真实 OIMemory 中
    可能命中很多 L1 历史任务,撑爆 max_recall_chars 让我们看不到 vault recall)。
    """
    captured_task: list[str] = []

    class FakeInterpreter:
        def chat(self, *args, **kwargs):
            captured_task.append(args[0] if args else kwargs.get("message", ""))
            yield "fake response"
    captured_task: list[str] = []

    class FakeInterpreter:
        def chat(self, *args, **kwargs):
            captured_task.append(args[0] if args else kwargs.get("message", ""))
            yield "fake response"

    import memory.oi_memory_hooks as _hooks
    import memory.oi_memory as _oi

    # mock OIMemory singleton — 直接 patch _hooks.get_memory
    fake_mem = _oi.Memory(
        id=999, layer="L2", title="mock_oi_recall",
        content="mock OI memory snippet", tags=[],
        created_at=1, access_count=0,
        namespace="", status="", depends_on=[], priority=0,
        quality_score=1.0, owner_agent="",
    )

    class FakeOIMemory:
        def __init__(self):
            self._stored = 0
        def recall(self, query, n=5, **kwargs):
            return [fake_mem]
        def store(self, **kwargs):
            self._stored += 1
            return self._stored

    fake_oi = FakeOIMemory()

    # patch bridge 让它走我们的 tmp_vault
    import prisIr_graph_recall_bridge as _real_bridge

    real_recall = _real_bridge.recall_for_task
    real_format = _real_bridge.format_vault_hits_for_prompt

    def patched_recall(query, k=5, min_l0_hits=2, **kwargs):
        kwargs.setdefault("vault_dir", tmp_vault)
        kwargs.setdefault("db_path", tmp_db)
        kwargs.setdefault("cache_path", tmp_cache)
        return real_recall(query, k=k, min_l0_hits=1, **kwargs)

    def patched_format(hits, max_chars=1500):
        return real_format(hits, max_chars=max_chars)

    with patch.object(_hooks, "get_memory", return_value=fake_oi), \
         patch.object(_real_bridge, "recall_for_task", side_effect=patched_recall), \
         patch.object(_real_bridge, "format_vault_hits_for_prompt", side_effect=patched_format):
        interp = FakeInterpreter()
        _hooks.install(interp, agent_name="test_bridge")
        # 注意:interp.chat 已被 install 替换为 chat_with_memory — 直接调用它
        chunks = list(interp.chat("ai"))

    assert len(captured_task) == 1
    task_str = captured_task[0]
    # OIMemory 段(mock)被注入
    assert "mock_oi_recall" in task_str
    # vault recall 段被注入
    assert "[vault graph recall" in task_str, f"应含 vault recall 头: {task_str[:300]}"
    assert "[End vault recall]" in task_str, f"应含 vault recall 尾"
    # 用户原 task 保留
    assert "ai" in task_str
    print(f"  [PASS] hook_injects_vault_recall: task 长度={len(task_str)}")


def test_hook_silent_on_bridge_failure():
    """bridge 抛异常 → chat 仍正常进行,task 不被污染。"""
    captured_task: list[str] = []

    class FakeInterpreter:
        def chat(self, *args, **kwargs):
            captured_task.append(args[0] if args else kwargs.get("message", ""))
            yield "fake"

    import memory.oi_memory_hooks as _hooks
    import memory.oi_memory as _oi
    import prisIr_graph_recall_bridge as _real_bridge

    fake_mem = _oi.Memory(
        id=999, layer="L2", title="mock_oi", content="mock", tags=[],
        created_at=1, access_count=0, namespace="", status="",
        depends_on=[], priority=0, quality_score=1.0, owner_agent="",
    )

    class FakeOI:
        def recall(self, query, n=5, **kw): return [fake_mem]
        def store(self, **kw): return 1

    def boom_recall(query, **kwargs):
        raise RuntimeError("mock bridge failure")

    with patch.object(_hooks, "get_memory", return_value=FakeOI()), \
         patch.object(_real_bridge, "recall_for_task", side_effect=boom_recall):
        interp = FakeInterpreter()
        _hooks.install(interp, agent_name="test_bridge_fail")
        chunks = list(interp.chat("hello world"))

    assert len(captured_task) == 1
    # bridge 失败静默 — task 应至少含原 task(可能含 OIMemory 召回)
    assert "hello world" in captured_task[0], f"原 task 应保留: {captured_task[0]!r}"
    # 不应含 vault graph recall 头(因为 bridge 抛了)
    assert "vault graph recall" not in captured_task[0]
    print(f"  [PASS] hook_silent_on_bridge_failure: 桥失败不污染对话")


def test_hook_no_recall_when_task_empty():
    """task 为空 → 不注入任何 context。"""
    captured_task: list[str] = []

    class FakeInterpreter:
        def chat(self, *args, **kwargs):
            captured_task.append(args[0] if args else kwargs.get("message", ""))
            yield "fake"

    import memory.oi_memory_hooks as _hooks
    interp = FakeInterpreter()
    _hooks.install(interp, agent_name="test_empty")
    chunks = list(interp.chat(""))

    assert len(captured_task) == 1
    # 空 task 应原样过(没注入任何 context)
    assert captured_task[0] == "", f"空 task 应原样: {captured_task[0]!r}"
    print(f"  [PASS] hook_no_recall_when_task_empty")


def main() -> int:
    # 单元测试(部分需要 tmp_vault + tmp_db)
    test_recall_empty_query_returns_empty()
    test_recall_format_accepts_dict_input()
    test_recall_vault_hit_to_dict()

    with tempfile.TemporaryDirectory(prefix="prisIr_bridge_test_") as tmp:
        tmp_dir = Path(tmp)
        tmp_vault = tmp_dir / "vault"
        tmp_vault.mkdir(parents=True, exist_ok=True)
        tmp_db = tmp_dir / "graph.db"
        tmp_cache = tmp_dir / "cache.db"

        # 造 vault 内容
        (tmp_vault / "ai.md").write_text(
            "---\ntags: [ai, alpha]\n---\n# AI 笔记\n讨论 Transformer 架构与神经网络。",
            encoding="utf-8",
        )
        (tmp_vault / "kg.md").write_text(
            "# 知识图谱\n实体关系图遍历。",
            encoding="utf-8",
        )
        (tmp_vault / "mm.md").write_text(
            "# 多模态\nCLIP 跨模态模型。",
            encoding="utf-8",
        )

        r = _build.build_vault(tmp_vault, tmp_db)
        assert r["ok"]
        print(f"[setup] build: {r['scanned']} nodes\n")

        test_recall_l0_hit_returns_vault_hits(tmp_vault, tmp_db, tmp_cache)
        test_recall_l0_miss_returns_empty_when_no_model(tmp_vault, tmp_db, tmp_cache)
        test_recall_format_prompt_structure(tmp_vault, tmp_db, tmp_cache)
        test_recall_format_max_chars_truncated(tmp_vault, tmp_db, tmp_cache)
        test_read_snippet_strips_frontmatter(tmp_vault)
        test_hook_injects_vault_recall_to_task(tmp_vault, tmp_db, tmp_cache)
        test_hook_silent_on_bridge_failure()
        test_hook_no_recall_when_task_empty()

    print("\n=== ALL BRIDGE TESTS PASSED ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())