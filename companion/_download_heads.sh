#!/bin/bash
# _download_heads.sh — M3.66 L3-C 8 head 下载到本地(2026-09-24)
# 从 aliyun T4 /workspace/agent-jev-output/{scene}_v3_export/{scene}_ch_head.pt
# 下载到本地 D:/prisir-train-assets/trained/{scene}_ch_v3/adapter/

set -e
ALIYUN=43.106.53.242
REMOTE_BASE=/workspace/agent-jev-output
LOCAL_BASE=/d/prisir-train-assets/trained

for SCENE in disk_cleanup tempfile email log perf intents task safety; do
  SRC="${REMOTE_BASE}/${SCENE}_v3_export/${SCENE}_ch_head.pt"
  DST="${LOCAL_BASE}/${SCENE}_ch_v3/adapter/${SCENE}_ch_head.pt"
  mkdir -p "$(dirname "$DST")"
  echo "=== 下载 ${SCENE} ==="
  scp "root@${ALIYUN}:${SRC}" "$DST" 2>&1 | tail -2
  ls -la "$DST" 2>&1
done

echo ""
echo "=== 8 head 下载完成 ==="
ls -la ${LOCAL_BASE}/*_ch_v3/adapter/*_ch_head.pt 2>&1