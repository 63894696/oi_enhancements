#!/bin/bash
# _ch_train_all.sh — M3.66 L3-C 8 个 AgentJev head 训练(2026-09-24 v4)
# 单 OOM 不影响后续 scenario
cd /workspace/agent-jev-repo

BACKBONE=/workspace/base_model
DATA_BASE=/workspace/companion/data/ch_out
OUTPUT_BASE=/workspace/agent-jev-output
CONFIG_BASE=/workspace/agent-jev-repo/configs

declare -A CANDIDATES
CANDIDATES[disk_cleanup]="safe,low,medium,high,critical"
CANDIDATES[tempfile]="safe,low,medium,high,critical"
CANDIDATES[email]="safe,low,medium,high,critical"
CANDIDATES[log]="safe,low,medium,high,critical"
CANDIDATES[perf]="safe,low,medium,high,critical"
CANDIDATES[intents]="chat,code,search,tool_call,roleplay"
CANDIDATES[task]="code_call,code_qa,creative,long,fast,general"
CANDIDATES[safety]="safe,unsafe"

# 8 个 yaml config(对齐 train.py cfg schema)
for SCENE in disk_cleanup tempfile email log perf intents task safety; do
  CAND="${CANDIDATES[$SCENE]}"
  CFG_FILE="${CONFIG_BASE}/${SCENE}_v3.yaml"
  OUT_DIR="${OUTPUT_BASE}/${SCENE}_v3"
  LOG_PATH="${OUT_DIR}/log.jsonl"
  CAND_JSON=$(python3 -c "import json; print(json.dumps('${CAND}'.split(',')))")

  cat > "$CFG_FILE" <<EOF
seed: 0
out_dir: ${OUT_DIR}
log_path: ${LOG_PATH}
log_reset: true
model_path: ${BACKBONE}
data_path: ${DATA_BASE}/data_${SCENE}_ch.jsonl
batch_states: 2
grad_accum: 4
max_len: 256
max_state_tokens: 128
max_steps: 500
warmup_ratio: 0.05
weight_decay: 0.01
grad_clip: 1.0
log_every: 20
max_consec_fail: 25
set_dim: 256
set_layers: 2
set_heads: 4
encoder_impl: path
margin: 0.5
lr:
  embed: 1.0e-5
  bottom: 2.0e-5
  middle: 4.0e-5
  top: 6.0e-5
  head: 1.0e-4
loss_weights:
  ce: 1.0
  brier: 0.0
  perm_kl: 0.0
save_every_steps: 100
save_final: true
EOF
done

echo "=== 8 个 yaml 写好 ==="
ls -la ${CONFIG_BASE}/*_v3.yaml
echo "=== sample yaml ==="
cat ${CONFIG_BASE}/disk_cleanup_v3.yaml

# 8 head 串行训
train_one() {
  local SCENE=$1
  local OUT_DIR="${OUTPUT_BASE}/${SCENE}_v3"

  echo ""
  echo "=========================================="
  echo "=== ${SCENE} head 训练 $(date) ==="
  echo "=========================================="
  nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader

  rm -rf "${OUT_DIR}" "${OUT_DIR}_export"
  mkdir -p "${OUT_DIR}_export"

  python3 /workspace/_ch_train_one.py \
    --config "${CONFIG_BASE}/${SCENE}_v3.yaml" \
    > "${OUTPUT_BASE}/${SCENE}_v3_train.log" 2>&1
  TRAIN_EXIT=$?

  if [ ! -f "${OUT_DIR}/final.pt" ]; then
    echo "❌ ${SCENE} 训练失败(exit=${TRAIN_EXIT})— final.pt 不存在"
    tail -30 "${OUTPUT_BASE}/${SCENE}_v3_train.log"
    return 1
  fi

  # 导出 head-only
  python3 -c "
import torch
ckpt = torch.load('${OUT_DIR}/final.pt', map_location='cpu', weights_only=False)
# 找 head 参数
head_state = {k: v for k, v in ckpt.items() if 'head' in k.lower() or k.startswith('set_encoder') or k.startswith('candidate')}
if not head_state:
    head_state = {k: v for k, v in ckpt.items() if not any(p in k for p in ['embed', 'layer.0', 'layer.1', 'norm'])}
torch.save({'state_dict': head_state, 'candidates': '${CANDIDATES[$SCENE]}'.split(','), 'schema': '${SCENE}'}, '${OUT_DIR}_export/${SCENE}_ch_head.pt')
print(f'✅ head-only export → ${SCENE}_ch_head.pt ({sum(v.numel() for v in head_state.values())} params)')
" 2>&1 | tee -a "${OUTPUT_BASE}/${SCENE}_v3_train.log"

  echo "=== ${SCENE} head 完成 $(date) ==="
  ls -la "${OUT_DIR}_export/" 2>&1 | tail -3
}

for SCENE in disk_cleanup tempfile email log perf intents task safety; do
  train_one "$SCENE" || echo "❌ $SCENE 训练失败 — 继续下一个"
done

echo ""
echo "==========================================="
echo "=== ALL 8 head 训练结束 $(date) ==="
echo "==========================================="
ls -la ${OUTPUT_BASE}/*_v3_export/*_ch_head.pt 2>&1