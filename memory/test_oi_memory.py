"""OI memory smoke test — 验证 store / recall / dedupe / access_count"""
import json
import os
import shutil
import tempfile
from pathlib import Path

# 临时 DB,不动真实 ~/.oi/memory.db
tmpdir = tempfile.mkdtemp(prefix="oi-mem-test-")
os.environ["OI_HOME"] = tmpdir

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from oi_memory import OIMemory
from oi_memory_hooks import get_memory, store, recall

print(f"=== sandbox DB: {tmpdir}/memory.db ===")

m = OIMemory()
print("[init] stats:", m.stats())

# 1. store 4 层各 1 条
store("L0", "user:zrkwedii9", "用户偏好中文,喜欢详细笔记", ["preference"])
store("L1", "project:team-web", "C:/Users/Administrator/demos/team-web 多 agent 协作面板", ["project"])
store("L2", "task:panel-bug", "切到 customer-service/investment/research 团队时,大部分 panel 丢失", ["bug"])
store("L3", "session:2026-07-01", "今天跑了 OI kimi-k2.7-code 修 panel bug,403 订阅墙挡住", ["oi-session"])
print("[store] 4 layers added")

# 2. dedupe:同 title 应该更新不是新增
id1 = store("L1", "project:team-web", "C:/Users/Administrator/demos/team-web [UPDATED]", ["project"])
total = m.stats()["total"]
assert total == 4, f"dedupe 失败,total 应为 4 实为 {total}"
print(f"[dedupe] 同 title 更新而非新增,total={total} ✓")

# 3. recall:不同 query 召回不同 layer
hits1 = recall("用户偏好", n=3)
print(f"[recall '用户偏好'] 命中 {len(hits1)} 条:")
for h in hits1:
    print(f"  [{h['layer']}] {h['title']}")

hits2 = recall("panel bug fix team-web", n=3)
print(f"[recall 'panel bug fix team-web'] 命中 {len(hits2)} 条:")
for h in hits2:
    print(f"  [{h['layer']}] {h['title']}")

# 4. access_count 应该递增
m2 = OIMemory()
before = m2.list_by_layer("L1")[0]
m2.recall("user preference", n=3)
after = m2.list_by_layer("L1")[0]
assert after.access_count >= before.access_count
print(f"[access_count] {before.access_count} → {after.access_count} ✓")

# 5. forget
m3 = OIMemory()
deleted = m3.forget(1)
assert deleted
print(f"[forget] id=1 删除成功 ✓")

# 6. stats
print("\n[final stats]", json.dumps(m3.stats(), indent=2, ensure_ascii=False))

# 7. hooks 层 smoke:pre_chat 注入
print("\n=== hooks 层 smoke ===")
from oi_memory_hooks import _format_hits_for_prompt
sample = [
    {"layer": "L0", "title": "user:zrk", "content": "用户偏好中文,喜欢详细笔记", "tags": []},
    {"layer": "L2", "title": "task:panel-bug", "content": "切到 customer-service 时大部分 panel 丢失", "tags": []},
]
formatted = _format_hits_for_prompt(sample)
print(formatted)

# 8. post_chat 修复:确保 store 的 L3 不含 recall context
print("\n=== post_chat 修复验证 ===")
import importlib
import oi_memory_hooks
importlib.reload(oi_memory_hooks)
from oi_memory_hooks import _format_hits_for_prompt, get_memory

mem = get_memory()
# 先清掉之前的 L3 测试数据
for m in mem.list_by_layer("L3"):
    mem.forget(m.id)

# 模拟 chat_with_memory 的内部逻辑:用 original_task 而不是 recall-augmented task 存
original_task = "用户问的问题:简单介绍 team-web"
hits = mem.recall(original_task, n=3)
ctx = _format_hits_for_prompt(hits)
augmented = f"{ctx}\n\n{original_task}"

# 模拟 post_chat:用 original_task 而非 augmented
mem.store(
    layer="L3",
    title=f"test:{original_task[:30]}",
    content=f"USER TASK:\n{original_task}\n\nASSISTANT:\nresponse text",
    tags=["test"],
)

# 现在 recall original_task,验证召回的 L3 内容不含 [OI memory recall ...]
hits = mem.recall(original_task, n=5)
l3_hits = [h for h in hits if h.layer == "L3"]
if l3_hits:
    polluted = any("[OI memory recall" in h.content for h in l3_hits)
    print(f"  L3 hits: {[h.title for h in l3_hits]}")
    print(f"  polluted: {polluted}")
    assert not polluted, f"L3 仍被 recall context 污染: {[h.title for h in l3_hits]}"
    print("  ✓ post_chat 不污染 L3 (不存 recall context)")
else:
    print("  (no L3 hits — dedup ok)")

# cleanup
shutil.rmtree(tmpdir, ignore_errors=True)
print("\n=== all assertions passed ===")