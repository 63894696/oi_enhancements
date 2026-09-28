#!/bin/bash
# _download_unified.sh — M3.66 L3 unified head 下载(2026-09-24)
# 从 aliyun T4 /workspace/agent-jev-output/unified_v3_export/unified_ch_head.pt
# 下载到本地 D:/prisir-train-assets/trained/unified_ch_v3/adapter/

set -e
ALIYUN=43.106.53.242
REMOTE=/workspace/agent-jev-output/unified_v3_export/unified_ch_head.pt
LOCAL=/d/prisir-train-assets/trained/unified_ch_v3/adapter/unified_ch_head.pt

mkdir -p "$(dirname "$LOCAL")"

echo "=== 检查 unified head 是否就绪 ==="
ssh -o ConnectTimeout=10 root@${ALIYUN} "ls -la ${REMOTE} 2>&1 | tail -1"

echo ""
echo "=== 下载 unified head (~30MB) ==="
scp "root@${ALIYUN}:${REMOTE}" "$LOCAL" 2>&1 | tail -2

echo ""
echo "=== 验证 ==="
ls -la "$LOCAL"
ssh -o ConnectTimeout=10 root@${ALIYUN} "ls -la /workspace/agent-jev-output/unified_v3/" 2>&1 | tail -8