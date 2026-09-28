#!/bin/bash
# _ch_convert_all.sh — T4 上跑 8 scenario jsonl → _ch.jsonl(AgentJev schema)
# 在 aliyun T4 /workspace 上跑
set -e
cd /workspace/companion
mkdir -p /workspace/companion/data/ch_out

# 5 个 v3 jsonl(M3.64 本地已上传 + 转)
for scene in disk_cleanup tempfile email log; do
  python3 data_prep_classification_head.py \
    --in data/data_${scene}_v3.jsonl \
    --out data/ch_out/data_${scene}_ch.jsonl \
    --schema $scene \
    --limit 800 2>&1 | tail -2
done

# perf 用 v3.jsonl
python3 data_prep_classification_head.py \
  --in data/data_perf_v3.jsonl \
  --out data/ch_out/data_perf_ch.jsonl \
  --schema perf \
  --limit 800 2>&1 | tail -2

# intents/task 从老 jsonl(M3.50/M3.58 数据)
python3 data_prep_classification_head.py \
  --in /workspace/data_intents.jsonl \
  --out data/ch_out/data_intents_ch.jsonl \
  --schema intents \
  --limit 600 2>&1 | tail -2

python3 data_prep_classification_head.py \
  --in /workspace/data_task.jsonl \
  --out data/ch_out/data_task_ch.jsonl \
  --schema task \
  --limit 600 2>&1 | tail -2

# safety(新造 120 条)
python3 data_prep_classification_head.py \
  --in data/data_safety.jsonl \
  --out data/ch_out/data_safety_ch.jsonl \
  --schema safety 2>&1 | tail -2

echo ""
echo "=== ch_out/ 现状 ==="
ls -la data/ch_out/