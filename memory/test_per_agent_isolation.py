"""P1 任务一:per-agent 记忆隔离接线测试

覆盖合约 §1.5:
- 单测(临时文件 DB):两个 owner 各 store 一条,验证 recall/list_by_layer 的
  visible_to 过滤三组语义(自己可见 / 他人不可见 / 不传=全部向后兼容)
- hooks 层单测:mock interpreter.chat,断言 pre_chat recall 收到
  visible_to=agent_name、post_chat store 收到 owner_agent=agent_name
- 冲突检查:现有库迁移后所有行 owner_agent='',旧调用结果与改造前一致
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from oi_memory import OIMemory  # noqa: E402
import oi_memory_hooks  # noqa: E402


@pytest.fixture()
def mem(tmp_path):
    """每个测试一个独立的临时文件 DB(比 :memory: 更贴近生产 WAL 路径)。"""
    return OIMemory(db_path=tmp_path / "memory.db")


# ============================================================================
# 1. oi_memory 层:visible_to 三组语义(合约 §1.5 单测)
# ============================================================================

def _seed(mem: OIMemory):
    """agent-a 私有一条 + agent-b 私有一条 + 全局共享一条,query 都能命中。"""
    id_a = mem.store(layer="L2", title="a-private", content="fixpanel bug alpha",
                     owner_agent="agent-a", dedupe_title=False)
    id_b = mem.store(layer="L2", title="b-private", content="fixpanel bug beta",
                     owner_agent="agent-b", dedupe_title=False)
    id_g = mem.store(layer="L2", title="global-shared", content="fixpanel bug gamma",
                     owner_agent="", dedupe_title=False)
    return id_a, id_b, id_g


def test_recall_visible_to_self_sees_own_and_global(mem):
    id_a, id_b, id_g = _seed(mem)
    hits = mem.recall("fixpanel bug", n=10, visible_to="agent-a")
    ids = {h.id for h in hits}
    assert id_a in ids, "agent-a 应能看到自己的私有条目"
    assert id_g in ids, "agent-a 应能看到全局共享条目"
    assert id_b not in ids, "agent-a 不应看到 agent-b 的私有条目"


def test_recall_visible_to_other_cannot_see_private(mem):
    id_a, id_b, id_g = _seed(mem)
    hits = mem.recall("fixpanel bug", n=10, visible_to="agent-b")
    ids = {h.id for h in hits}
    assert id_b in ids
    assert id_g in ids
    assert id_a not in ids, "agent-b 不应看到 agent-a 的私有条目"


def test_recall_without_visible_to_sees_all_backward_compat(mem):
    id_a, id_b, id_g = _seed(mem)
    hits = mem.recall("fixpanel bug", n=10)  # 不传 visible_to
    ids = {h.id for h in hits}
    assert ids == {id_a, id_b, id_g}, "不传 visible_to 应命中全部(向后兼容)"


def test_list_by_layer_visible_to_self(mem):
    id_a, id_b, id_g = _seed(mem)
    items = mem.list_by_layer("L2", limit=20, visible_to="agent-a")
    ids = {m.id for m in items}
    assert id_a in ids and id_g in ids
    assert id_b not in ids


def test_list_by_layer_visible_to_other(mem):
    id_a, id_b, id_g = _seed(mem)
    items = mem.list_by_layer("L2", limit=20, visible_to="agent-b")
    ids = {m.id for m in items}
    assert id_b in ids and id_g in ids
    assert id_a not in ids


def test_list_by_layer_without_visible_to_backward_compat(mem):
    id_a, id_b, id_g = _seed(mem)
    items = mem.list_by_layer("L2", limit=20)
    ids = {m.id for m in items}
    assert ids == {id_a, id_b, id_g}


# ============================================================================
# 2. 冲突检查:迁移默认 owner_agent='' + 旧调用结果与改造前一致
# ============================================================================

def test_legacy_rows_default_owner_agent_empty(mem):
    """模拟老库:直接 SQL 插入不指定 owner_agent,迁移默认应为 ''。"""
    # store 不传 owner_agent → 默认 ''
    mid = mem.store(layer="L1", title="legacy", content="legacy row content")
    conn = sqlite3.connect(str(mem.db_path))
    try:
        row = conn.execute("SELECT owner_agent FROM memories WHERE id=?", (mid,)).fetchone()
        assert row[0] == "", f"旧调用 store 后 owner_agent 应为 ''(全局),实为 {row[0]!r}"
    finally:
        conn.close()


def test_legacy_recall_unchanged_after_migration(mem):
    """旧调用(全部不传隔离参数)召回结果与改造前完全一致。"""
    m1 = mem.store(layer="L2", title="t1", content="keyword alpha", dedupe_title=False)
    m2 = mem.store(layer="L2", title="t2", content="keyword beta", dedupe_title=False)
    hits = mem.recall("keyword", n=10)
    assert {h.id for h in hits} == {m1, m2}
    items = mem.list_by_layer("L2", limit=10)
    assert {m.id for m in items} == {m1, m2}


# ============================================================================
# 3. hooks 层:install 后 pre_chat/post_chat 传隔离参数
# ============================================================================

class _FakeInterpreter:
    def __init__(self):
        self.chat_calls = []

    def chat(self, *args, **kwargs):
        self.chat_calls.append((args, kwargs))
        yield {"type": "message", "content": "assistant reply text"}


def test_hooks_post_chat_store_owner_agent(monkeypatch, tmp_path):
    mem = OIMemory(db_path=tmp_path / "memory.db")
    monkeypatch.setattr(oi_memory_hooks, "_DEFAULT_MEM", mem)

    interp = _FakeInterpreter()
    oi_memory_hooks.install(interp, agent_name="oi-coder", recall_n=3)

    # 清掉 install() 里的 L0 store 调用记录
    calls_before = []
    orig_store = mem.store
    monkeypatch.setattr(mem, "store",
                        lambda **kw: calls_before.append(kw) or orig_store(**kw))

    list(interp.chat("how to fix panel bug"))

    # pre_chat 的 L3 快照 store(post_chat)必须带 owner_agent
    post_chat_stores = [c for c in calls_before if c.get("layer") == "L3"]
    assert post_chat_stores, "post_chat 应有一次 L3 store"
    for c in post_chat_stores:
        assert c.get("owner_agent") == "oi-coder", \
            f"post_chat store 应收到 owner_agent='oi-coder',实为 {c.get('owner_agent')!r}"


def test_hooks_pre_chat_recall_visible_to(monkeypatch, tmp_path):
    mem = OIMemory(db_path=tmp_path / "memory.db")
    monkeypatch.setattr(oi_memory_hooks, "_DEFAULT_MEM", mem)

    recall_kwargs = []
    orig_recall = mem.recall

    def spy_recall(query, **kwargs):
        recall_kwargs.append(kwargs)
        return orig_recall(query, **kwargs)

    monkeypatch.setattr(mem, "recall", spy_recall)

    interp = _FakeInterpreter()
    oi_memory_hooks.install(interp, agent_name="oi-writer", recall_n=4)
    list(interp.chat("summarize the project status"))

    assert recall_kwargs, "pre_chat 应调用 mem.recall"
    for kw in recall_kwargs:
        assert kw.get("visible_to") == "oi-writer", \
            f"pre_chat recall 应收到 visible_to='oi-writer',实为 {kw.get('visible_to')!r}"


# ============================================================================
# 4. MCP 路径:session_tools 的 agent_id 透传(合约 §1.3 改动点 2)
# ============================================================================

def test_session_tools_agent_id_passthrough(monkeypatch, tmp_path):
    """memory_namespace_set/list 的 agent_id 自报应透传到 store/list_by_layer。"""
    import json as _json

    mcp_dir = Path("C:/Users/Administrator/oi_enhancements/mcp_prisiragent_server")
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    import session_tools  # noqa: E402

    mem = OIMemory(db_path=tmp_path / "memory.db")

    # 让 session_tools 内部的 `from memory.oi_memory import OIMemory` 拿到我们的临时库
    fake_mod = type(sys)("memory.oi_memory")
    fake_mod.OIMemory = lambda *a, **kw: mem  # noqa: E731
    monkeypatch.setitem(sys.modules, "memory.oi_memory", fake_mod)

    # agent-a 注册私有 namespace;再注册一个全局 namespace
    out_a = _json.loads(session_tools.memory_namespace_set_impl(
        namespace="ns-a-private", agent_id="agent-a"))
    assert out_a["ok"] is True
    out_g = _json.loads(session_tools.memory_namespace_set_impl(
        namespace="ns-global", agent_id=""))
    assert out_g["ok"] is True

    # agent-b list:应只见全局,不见 agent-a 私有
    lst_b = _json.loads(session_tools.memory_namespace_list_impl(agent_id="agent-b"))
    names_b = {n["name"] for n in lst_b["namespaces"]}
    assert "ns-global" in names_b
    assert "ns-a-private" not in names_b, "agent-b 不应看到 agent-a 私有 namespace"

    # agent-a list:两者都见
    lst_a = _json.loads(session_tools.memory_namespace_list_impl(agent_id="agent-a"))
    names_a = {n["name"] for n in lst_a["namespaces"]}
    assert {"ns-global", "ns-a-private"} <= names_a

    # 不传 agent_id:向后兼容,全部可见
    lst_all = _json.loads(session_tools.memory_namespace_list_impl())
    names_all = {n["name"] for n in lst_all["namespaces"]}
    assert {"ns-global", "ns-a-private"} <= names_all
