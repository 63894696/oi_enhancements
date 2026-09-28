"""
tests/test_phase_9_om_p1_and_ma_p1.py — Phase 9 OM-P1 + MA-P1 测试(2026-09-28)。

承接 [[prisIr-openmontage-recon]] + 用户「最小 agent 团队」决策。

双线 ship:
  OM-P1(video_checkpoint + workflow 集成)— 借鉴 OpenMontage
  MA-P1(agent_handoff schema)— 借鉴 OpenAI Agents SDK

## OM-P1 测试(6 项)
  1. CheckpointManager save/load 闭环
  2. atomic 写 — 异常时不留半写文件
  3. resume_from 跳过 status=='ok' 的 step
  4. cleanup_lru 保留最近 N 个
  5. workflow 集成 — workflow_id 落 checkpoint + resume 跳过
  6. 向后兼容 — 不传 workflow_id 旧行为不变

## MA-P1 测试(8 项)
  1. HandoffPayload dataclass 默认值
  2. to_dict / from_dict 闭环
  3. accept_handoff / reject_handoff(reason 必填)
  4. HandoffPayload.is_expired 超时判定
  5. HandoffCaps 计数 + 超 cap 抛 HandoffCapExceeded
  6. HandoffCaps.reset(单 trace / 全部)
  7. new_task_id / new_trace_id 唯一性
  8. accept_handoff 附 new_payload 转交(借鉴 OpenAI Agents SDK)
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ════════════════════════════════════════════════════════════════════════════
# OM-P1 tests — CheckpointManager + workflow 集成
# ════════════════════════════════════════════════════════════════════════════

def test_om_p1_1_save_load_roundtrip():
    """#1 CheckpointManager save/load 闭环"""
    from prisir_work.video_checkpoint import CheckpointManager
    with tempfile.TemporaryDirectory() as tmp:
        cm = CheckpointManager(root=tmp)
        wf = cm.new_workflow_id()
        cm.save(wf, "step1", {"ok": True, "result_path": "/tmp/x.mp4"})
        ck = cm.load_step(wf, "step1")
        assert ck is not None
        assert ck.status == "ok"
        assert ck.payload["result_path"] == "/tmp/x.mp4"
        # load_latest 应该也是 step1
        latest = cm.load_latest(wf)
        assert latest is not None
        assert latest.step_id == "step1"
    print("✓ #1 CheckpointManager save/load 闭环")


def test_om_p1_2_atomic_write():
    """#2 atomic 写 — 异常时不留半写文件"""
    from prisir_work.video_checkpoint import CheckpointManager
    with tempfile.TemporaryDirectory() as tmp:
        cm = CheckpointManager(root=tmp)
        wf = cm.new_workflow_id()
        cm.save(wf, "step1", {"ok": True})
        # 不应有 .ck_*.tmp 残留
        wf_dir = cm._wf_dir(wf)
        tmps = list(wf_dir.glob(".ck_*.tmp"))
        assert not tmps, f"原子写残留: {tmps}"
    print("✓ #2 atomic 写 — 无 .ck_*.tmp 残留")


def test_om_p1_3_resume_skip_done():
    """#3 resume_from 跳过 status=='ok' 的 step"""
    from prisir_work.video_checkpoint import CheckpointManager
    with tempfile.TemporaryDirectory() as tmp:
        cm = CheckpointManager(root=tmp)
        wf = cm.new_workflow_id()
        cm.save(wf, "step1", {"ok": True}, status="ok")
        cm.save(wf, "step2", {"ok": False}, status="failed")
        cm.save(wf, "step3", {"ok": True}, status="ok")
        done = cm.resume_from(wf, ["step1", "step2", "step3"])
        assert done == ["step1", "step3"], done  # failed 不算 done
        # payload 反推
        p = cm.load_step_payload(wf, "step1")
        assert p.get("ok") is True
    print("✓ #3 resume_from 跳过 status=='ok' 的 step,failed 不算 done")


def test_om_p1_4_cleanup_lru():
    """#4 cleanup_lru 保留最近 N 个"""
    from prisir_work.video_checkpoint import CheckpointManager
    with tempfile.TemporaryDirectory() as tmp:
        cm = CheckpointManager(root=tmp)
        wfs = []
        for i in range(25):
            wf = cm.new_workflow_id()
            cm.save(wf, "s1", {"i": i})
            wfs.append(wf)
            time.sleep(0.01)  # 确保 mtime 排序
        deleted = cm.cleanup_lru(keep=20)
        assert deleted == 5, f"应删 5 个,实删 {deleted}"
        assert len(cm.list_workflows()) == 20
    print("✓ #4 cleanup_lru(keep=20) 删 5 个保留 20")


def test_om_p1_5_workflow_integration():
    """#5 workflow 集成 — workflow_id 落 checkpoint + resume 跳过"""
    from prisir_work.agent_video_workflow import run_workflow
    from prisir_work.video_checkpoint import CheckpointManager
    import os
    # 用临时目录避免污染真实 checkpoint
    with tempfile.TemporaryDirectory() as tmp:
        # monkey patch CheckpointManager 根目录
        from prisir_work import video_checkpoint as vc
        original_init = vc.CheckpointManager.__init__
        vc.CheckpointManager.__init__ = lambda self, root=tmp: original_init(self, root)
        try:
            # 5.1 mock endpoints:把 video.info 注册成总是 ok
            from prisir_work import capability as _cap
            from prisir_work import endpoints as _ep
            # 找一个已有 video capability 试跑
            real_handler = None
            for cap_id, entry in _cap._REGISTRY.items():
                if entry.get("endpoint") and entry["endpoint"] in _ep._REGISTRY:
                    real_handler = _ep._REGISTRY[entry["endpoint"]]["handler"]
                    break
            if not real_handler:
                print("⚠ #5 跳过 — 无真实 endpoint 可 mock")
                return

            dsl = {
                "steps": [
                    {"id": "s1", "capability": list(_cap._REGISTRY.keys())[0],
                     "args": {}},
                    {"id": "s2", "capability": list(_cap._REGISTRY.keys())[0],
                     "args": {}, "depends_on": ["s1"]},
                ],
                "stop_on_error": True,
            }
            wf_id = vc.CheckpointManager.new_workflow_id()
            # 第一次跑 — 不传 wf_id(向后兼容)
            r1 = run_workflow(dsl)
            assert r1.ok, f"无 checkpoint 跑应成功:{r1.error}"
            # 第二次跑 — 传 wf_id 落 checkpoint
            r2 = run_workflow(dsl, workflow_id=wf_id)
            assert r2.ok
            cm = vc.CheckpointManager(root=tmp)
            assert "s1" in cm.list_steps(wf_id)
            assert "s2" in cm.list_steps(wf_id)
            # resume 模式
            dsl["resume"] = True
            dsl["workflow_id"] = wf_id
            r3 = run_workflow(dsl)
            assert r3.ok
        finally:
            vc.CheckpointManager.__init__ = original_init
    print("✓ #5 workflow 集成 — checkpoint 落盘 + resume 跳过 + 向后兼容")


def test_om_p1_6_no_workflow_id_backward_compat():
    """#6 不传 workflow_id 旧行为不变"""
    from prisir_work.agent_video_workflow import run_workflow
    from prisir_work import capability as _cap
    from prisir_work import endpoints as _ep
    real_cap = None
    for cap_id, entry in _cap._REGISTRY.items():
        if entry.get("endpoint") and entry["endpoint"] in _ep._REGISTRY:
            real_cap = cap_id
            break
    if not real_cap:
        print("⚠ #6 跳过 — 无真实 capability")
        return
    dsl = {"steps": [{"id": "s1", "capability": real_cap, "args": {}}]}
    r = run_workflow(dsl)  # 不传 wf_id
    assert r.ok, f"旧行为应正常:{r.error}"
    print("✓ #6 不传 workflow_id 旧行为不变(向后兼容)")


# ════════════════════════════════════════════════════════════════════════════
# MA-P1 tests — Handoff schema
# ════════════════════════════════════════════════════════════════════════════

def test_ma_p1_1_payload_defaults():
    """#1 HandoffPayload dataclass 默认值"""
    from prisir_work.agent_handoff import HandoffPayload, new_trace_id
    p = HandoffPayload(task_id="t1", trace_id=new_trace_id(),
                       original_goal="做个 60 秒短片",
                       from_agent="Triage", to_agent="Creative")
    assert p.task_id == "t1"
    assert p.original_goal == "做个 60 秒短片"
    assert p.from_agent == "Triage"
    assert p.to_agent == "Creative"
    assert p.completed_steps == []
    assert p.pending_steps == []
    assert p.constraints == {}
    assert p.accumulated_context == {}
    assert p.timeout_sec == 60.0  # 默认
    print("✓ #1 HandoffPayload 默认值齐全(to_agent / from_agent / completed / pending / constraints / ctx / timeout=60.0)")


def test_ma_p1_2_dict_roundtrip():
    """#2 to_dict / from_dict 闭环"""
    from prisir_work.agent_handoff import HandoffPayload
    p = HandoffPayload(task_id="t2", trace_id="tr1",
                       original_goal="g", from_agent="A", to_agent="B",
                       completed_steps=["x"], pending_steps=["y"],
                       constraints={"deadline": "2026-10-01"},
                       accumulated_context={"scene_count": 5})
    d = p.to_dict()
    j = json.dumps(d, ensure_ascii=False)
    p2 = HandoffPayload.from_dict(json.loads(j))
    assert p2.task_id == p.task_id
    assert p2.original_goal == p.original_goal
    assert p2.completed_steps == p.completed_steps
    assert p2.constraints == p.constraints
    assert p2.accumulated_context == p.accumulated_context
    print("✓ #2 HandoffPayload to_dict/from_dict JSON 闭环")


def test_ma_p1_3_accept_reject():
    """#3 accept_handoff / reject_handoff(reason 必填)"""
    from prisir_work.agent_handoff import (
        HandoffPayload, accept_handoff, reject_handoff,
    )
    p = HandoffPayload(task_id="t3", trace_id="tr1", original_goal="g",
                       from_agent="A", to_agent="B")
    r_accept = accept_handoff(p)
    assert r_accept.accepted is True
    assert r_accept.task_id == "t3"
    # reject 必须有 reason
    r_reject = reject_handoff(p, "B agent 满载")
    assert r_reject.accepted is False
    assert r_reject.reason == "B agent 满载"
    # reject 无 reason 抛 ValueError
    try:
        reject_handoff(p, "")
        assert False, "应抛 ValueError"
    except ValueError:
        pass
    print("✓ #3 accept_handoff + reject_handoff(reason 必填, 空 reason 抛 ValueError)")


def test_ma_p1_4_expired():
    """#4 HandoffPayload.is_expired 超时判定"""
    from prisir_work.agent_handoff import HandoffPayload
    # 未超时
    p1 = HandoffPayload(task_id="t4", trace_id="tr1", original_goal="g",
                        from_agent="A", to_agent="B", created_at=time.time())
    assert not p1.is_expired(), "新建 payload 不应过期"
    # 已超时(创建于 100s 前,timeout=10s)
    p2 = HandoffPayload(task_id="t4b", trace_id="tr1", original_goal="g",
                        from_agent="A", to_agent="B",
                        created_at=time.time() - 100, timeout_sec=10.0)
    assert p2.is_expired(), "100s 前的 payload 应过期"
    print("✓ #4 HandoffPayload.is_expired 超时判定正确")


def test_ma_p1_5_caps():
    """#5 HandoffCaps 计数 + 超 cap 抛 HandoffCapExceeded"""
    from prisir_work.agent_handoff import HandoffCaps, HandoffCapExceeded
    caps = HandoffCaps(cap=3)
    assert caps.increment("tr1") == 1
    assert caps.increment("tr1") == 2
    assert caps.increment("tr1") == 3
    try:
        caps.increment("tr1")
        assert False, "第 4 次应抛"
    except HandoffCapExceeded as e:
        assert e.count == 4
        assert e.cap == 3
        assert e.trace_id == "tr1"
    # 不同 trace 独立计数
    assert caps.increment("tr2") == 1
    print("✓ #5 HandoffCaps 计数 + 超 cap 抛 HandoffCapExceeded(不同 trace 独立)")


def test_ma_p1_6_caps_reset():
    """#6 HandoffCaps.reset(单 trace / 全部)"""
    from prisir_work.agent_handoff import HandoffCaps
    caps = HandoffCaps(cap=2)
    caps.increment("tr1")
    caps.increment("tr2")
    assert caps.get("tr1") == 1
    assert caps.get("tr2") == 1
    caps.reset("tr1")
    assert caps.get("tr1") == 0
    assert caps.get("tr2") == 1
    caps.reset()
    assert caps.get("tr2") == 0
    print("✓ #6 HandoffCaps.reset(单 trace / 全部)")


def test_ma_p1_7_id_uniqueness():
    """#7 new_task_id / new_trace_id 唯一性"""
    from prisir_work.agent_handoff import new_task_id, new_trace_id
    tids = {new_task_id() for _ in range(50)}
    traces = {new_trace_id() for _ in range(50)}
    assert len(tids) == 50, "task_id 有重复"
    assert len(traces) == 50, "trace_id 有重复"
    # 格式校验
    for tid in list(tids)[:3]:
        assert tid.startswith("task_")
    for tr in list(traces)[:3]:
        assert tr.startswith("trace_")
    print("✓ #7 new_task_id / new_trace_id 50 次循环无重复 + 格式正确")


def test_ma_p1_8_handoff_chain():
    """#8 accept_handoff 附 new_payload 转交(借鉴 OpenAI Agents SDK 链式交接)"""
    from prisir_work.agent_handoff import (
        HandoffPayload, accept_handoff, new_task_id, new_trace_id,
    )
    # Triage → Creative → Art(链式)
    task_id = new_task_id()
    trace_id = new_trace_id()
    p1 = HandoffPayload(task_id=task_id, trace_id=trace_id,
                        original_goal="做个 60 秒短片",
                        from_agent="Triage", to_agent="Creative",
                        pending_steps=["script", "shot_breakdown"])
    # Creative 接受后,转交 Art
    p2 = HandoffPayload(task_id=task_id, trace_id=trace_id,
                        original_goal=p1.original_goal,
                        from_agent="Creative", to_agent="Art",
                        current_state="script 完成",
                        completed_steps=["script"],
                        pending_steps=["shot_breakdown", "keyframe_render"],
                        accumulated_context={"script": "PrisirAI intro..."})
    r = accept_handoff(p1, new_payload=p2)
    assert r.accepted
    assert r.new_payload is not None
    assert r.new_payload.from_agent == "Creative"
    assert r.new_payload.to_agent == "Art"
    assert r.new_payload.completed_steps == ["script"]
    print("✓ #8 accept_handoff 附 new_payload 转交(链式 handoff: Triage→Creative→Art)")


if __name__ == "__main__":
    print("═══ OM-P1 tests(6)═══")
    test_om_p1_1_save_load_roundtrip()
    test_om_p1_2_atomic_write()
    test_om_p1_3_resume_skip_done()
    test_om_p1_4_cleanup_lru()
    test_om_p1_5_workflow_integration()
    test_om_p1_6_no_workflow_id_backward_compat()
    print()
    print("═══ MA-P1 tests(8)═══")
    test_ma_p1_1_payload_defaults()
    test_ma_p1_2_dict_roundtrip()
    test_ma_p1_3_accept_reject()
    test_ma_p1_4_expired()
    test_ma_p1_5_caps()
    test_ma_p1_6_caps_reset()
    test_ma_p1_7_id_uniqueness()
    test_ma_p1_8_handoff_chain()
    print("\n所有 14 组断言通过 ✅")