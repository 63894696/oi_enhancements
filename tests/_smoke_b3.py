#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2.5+B-3 烟雾脚本:真服务 + 真 task-runner + fire-and-forget + progress 流 + cancel
跑通整套链路,给用户手工看 B-3 真跑前的快速验证。
"""
import json, time, urllib.request, sys

BASE = "http://127.0.0.1:18899"

def rpc(method, params, timeout=8):
    body = json.dumps({"ext_id": "task-runner", "method": method,
                       "params": params, "timeout": timeout}).encode("utf-8")
    req = urllib.request.Request(BASE + "/prisiragent/api/ext/rpc",
                                  data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout + 2) as r:
        return json.loads(r.read().decode("utf-8"))

def post_progress(run_id, since=0, ack=False):
    body = json.dumps({"run_id": run_id, "since": since, "ack": ack}).encode("utf-8")
    req = urllib.request.Request(BASE + "/prisiragent/api/workflow/run_progress",
                                  data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))

print("=" * 60)
print("B-3 烟雾测试: task-runner 任务队列升级")
print("=" * 60)

# 1. 创建带重试 + timeout 的 task(目标 ext 不存在 → 必失败 + 触发 progress 推送 + node_runs 落库)
print("\n[1/5] task.upsert: 创建 2 节点 DAG (retries + timeout)")
dag = {
    "n1": {"ext": "never-exists", "method": "echo", "params": {"msg": "step1"},
           "retry": {"max_retries": 2, "backoff": "constant", "timeout_sec": 2}},
    "n2": {"ext": "never-exists", "method": "echo2", "params": {"msg": "step2"},
           "needs": ["n1"],
           "retry": {"max_retries": 1, "backoff": "constant", "timeout_sec": 2}},
}
r = rpc("task.upsert", {"name": "b3-smoke-demo", "dag": dag})
task_id = r["result"]["id"]
print(f"  ✓ task_id = {task_id}")

# 2. fire-and-forget run
print("\n[2/5] task.run wait=false: 拿 run_id + 后台跑")
r = rpc("task.run", {"id": task_id, "wait": False}, timeout=5)
print(f"  ✓ {r}")
run_id = r["result"]["run_id"]
print(f"  run_id = {run_id}")

# 3. 立刻拉一次 progress (n1 节点可能已在跑)
print("\n[3/5] /api/workflow/run_progress: 长轮询拿 progress")
seen_seq = 0
all_items = []
for i in range(8):
    time.sleep(1)
    pr = post_progress(run_id, since=seen_seq, ack=True)
    items = pr.get("items", [])
    if items:
        for it in items:
            all_items.append(it)
            print(f"    [{i}s] node={it.get('node_id'):>3} status={it.get('status'):>9} attempts={it.get('attempts',0)} ms={it.get('ms',0)} event={it.get('event','')}")
        seen_seq = pr.get("seq", seen_seq)
    if pr.get("missing"):
        print(f"    queue missing (run 已结束 / 队列清空)")
        break
print(f"  → 共收到 {len(all_items)} 条 progress")
print(f"  → 最终 seq = {seen_seq}")

# 4. 查 runs + node_runs 持久化
print("\n[4/5] task.runs: 查 node_runs 表持久化")
r = rpc("task.runs", {"task_id": task_id, "limit": 1})
runs = r["result"]["runs"]
if runs:
    run = runs[0]
    rid = run.get('id') or run.get('run_id')
    print(f"  run_id={rid} status={run['status']} ms={run.get('ms',0)}")
    nodes = run.get("result", {}).get("nodes", {})
    for nid, ns in nodes.items():
        print(f"    node {nid}: status={ns.get('status')} attempts={ns.get('attempts')} ms={ns.get('ms')} error={ns.get('error','')[:60]}")
else:
    print("  (无 runs 记录)")

# 5. 模拟 cancel:重新跑一个,fire-and-forget 立刻 cancel
print("\n[5/5] task.run.cancel: 测真取消")
dag2 = {
    "slow": {"ext": "never-exists", "method": "sleep",
             "params": {"ms": 5000},
             "retry": {"max_retries": 5, "backoff": "constant", "timeout_sec": 8}},
}
r = rpc("task.upsert", {"name": "b3-cancel-demo", "dag": dag2})
tid2 = r["result"]["id"]
r = rpc("task.run", {"id": tid2, "wait": False}, timeout=5)
rid2 = r["result"]["run_id"]
print(f"  fire-and-forget run rid={rid2}")
time.sleep(0.3)  # 等节点开始
r = rpc("task.run.cancel", {"run_id": rid2}, timeout=5)
print(f"  cancel: {r}")
# 等当前 attempt 结束(timeout_sec=8) + cancel 检测 + 落库
for wait in (1, 3, 6, 9):
    time.sleep(wait - (0 if wait == 1 else (1, 3, 6, 9)[([1,3,6,9].index(wait)-1)]))
    r = rpc("task.runs", {"task_id": tid2, "limit": 1})
    if not r["result"]["runs"]: continue
    run = r["result"]["runs"][0]
    print(f"    [T+{wait}s] run status={run['status']} finished_at={run.get('finished_at',0)}")
    nodes = run.get("result", {}).get("nodes", {})
    for nid, ns in nodes.items():
        print(f"      node {nid}: status={ns.get('status')} attempts={ns.get('attempts')} error={(ns.get('error','') or '')[:80]}")
    if run["status"] in ("canceled", "failed", "ok"):
        print(f"  final status={run['status']}")
        break
nodes = run.get("result", {}).get("nodes", {})
for nid, ns in nodes.items():
    print(f"    node {nid}: status={ns.get('status')} attempts={ns.get('attempts')} error={ns.get('error','')[:60]}")

print("\n" + "=" * 60)
print("✓ B-3 烟雾测试全跑通")
print("=" * 60)