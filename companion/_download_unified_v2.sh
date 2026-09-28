#!/bin/bash
# _download_unified_v2.sh — 下载 unified v2 head 到本地(2026-09-24)
set -e

ALIYUN=root@43.106.53.242
REMOTE=/workspace/agent-jev-output/unified_v3_v2_export/unified_ch_v2_head.pt
LOCAL_DIR=/d/prisir-train-assets/trained/unified_ch_v3_v2/adapter
LOCAL_FILE=$LOCAL_DIR/unified_ch_v2_head.pt

mkdir -p "$LOCAL_DIR"

scp -o ConnectTimeout=10 "$ALIYUN:$REMOTE" "$LOCAL_FILE"

if [ -f "$LOCAL_FILE" ]; then
    size_mb=$(du -m "$LOCAL_FILE" | cut -f1)
    echo "✅ downloaded: $LOCAL_FILE ($size_mb MB)"
else
    echo "❌ download failed"
    exit 1
fi