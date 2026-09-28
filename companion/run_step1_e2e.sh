#!/bin/bash
# run_step1_e2e.sh — 端到端串起来:本机下 → 实例起 → scp → 训练 → 回传 → 销毁
#
# 用法(本地):
#   ./run_step1_e2e.sh
#
# 设计:
#   1) 本机 download_assets_local.py 下好 Qwen3Guard 模型 + 数据 → assets.tar.gz
#   2) aliyun_train_cn.sh start 起 cn-shanghai P4 实例
#   3) 等待 RUNNING + 公网 IP
#   4) scp assets.tar.gz + train_step1.py + deploy_local_guard.py → /workspace/
#   5) ssh 上实例解压 + 跑训练(~30 min)
#   6) scp LoRA adapter + merged + .gguf 回本地
#   7) destroy 实例释放 EIP + 存储
#
# 预算:实例 ~¥8.68/h × 0.7h ≈ ¥6 + 存储 + 公网 ≈ ¥10 总开销
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
# Git Bash 下 $(cd) 返 MSYS 路径(/c/...),Python 不认,转 Windows 路径
HERE_WIN=$(cygpath -w "$HERE" 2>/dev/null || echo "$HERE")
ASSETS_DIR="${ASSETS_DIR:-D:/prisir-train-assets}"
ASSETS_DIR_WIN=$(cygpath -w "$ASSETS_DIR" 2>/dev/null || echo "$ASSETS_DIR")
PYTHON="${PYTHON:-python}"
SSH_PASS="${PRISIR_TRAIN_PASS:-PrisirTrain2026!}"
SSH_PASS_OPT=""
if command -v sshpass &> /dev/null; then
  SSH_PASS_OPT="sshpass -p $SSH_PASS"
fi

step() { echo ""; echo "===> $*"; }

step "1/7 本机下载资产到 $ASSETS_DIR"
"$PYTHON" "$HERE_WIN\\download_assets_local.py" --output-dir "$ASSETS_DIR_WIN"

step "2/7 起 cn-shanghai P4 实例"
"$HERE/aliyun_train_cn.sh" start

step "3/7 等待实例 RUNNING(每 10s 探一次,最多 5 分钟)"
IP=""
for i in $(seq 1 30); do
  sleep 10
  IP=$("$HERE/aliyun_train_cn.sh" status 2>&1 | \
       python -c "
import sys
for line in sys.stdin:
    line = line.strip()
    if 'pub_ip=' in line:
        v = line.split('pub_ip=')[1].split()[0]
        if v and v != '(no public ip)':
            print(v); break
")
  STATUS=$("$HERE/aliyun_train_cn.sh" status 2>&1 | \
           python -c "
import sys
for line in sys.stdin:
    line = line.strip()
    if 'status=' in line:
        v = line.split('status=')[1].split()[0]
        print(v); break
")
  if [ "$STATUS" = "Running" ] && [ -n "$IP" ]; then
    echo "✅ Running at $IP"
    break
  fi
  echo "  [$i/30] status=$STATUS ip=$IP,再等 10s..."
done
if [ -z "$IP" ] || [ "$STATUS" != "Running" ]; then
  echo "❌ 实例 5 分钟没起来,手动 ssh 排查"
  exit 1
fi

step "4/7 scp 上传资产到 root@$IP:/workspace/"
$SSH_PASS_OPT scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "$ASSETS_DIR/assets.tar.gz" root@"$IP":/workspace/
$SSH_PASS_OPT scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  "$HERE/train_step1.py" root@"$IP":/workspace/
echo "✅ uploaded"

step "5/7 SSH 上实例:解压 + 跑训练(~30 min,后台 nohup 防 SSH 断)"
$SSH_PASS_OPT ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  root@"$IP" <<'REMOTE'
set -e
mkdir -p /workspace && cd /workspace
tar -xzf assets.tar.gz
source /opt/prisirt-venv/bin/activate
nvidia-smi

# nohup + setsid 防 SSH 断开杀掉训练
cat > /workspace/run_train.sh <<'INNER'
#!/bin/bash
set -e
cd /workspace
source /opt/prisirt-venv/bin/activate
python train_step1.py \
  --data /workspace/data/data_step1.jsonl \
  --base-model /workspace/models/Qwen3Guard-Gen-0.6B \
  --output /workspace/qwen3guard-finetuned \
  --epochs 3 --batch 4 --quant-gguf
INNER
chmod +x /workspace/run_train.sh

# 启动后台训练,完全脱离 SSH 父进程
setsid nohup /workspace/run_train.sh \
  > /workspace/train.log 2>&1 < /dev/null &
TRAIN_PID=$!
echo $TRAIN_PID > /workspace/train.pid
disown
echo "✅ 训练后台启动 PID=$TRAIN_PID,日志:tail -f /workspace/train.log"
REMOTE

step "6/7 等待训练完成(轮询直到 train.log 出现 'end' 或 train.pid 退出)"
for i in $(seq 1 60); do
  sleep 30
  STATUS=$($SSH_PASS_OPT ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null root@"$IP" \
    "ps -p \$(cat /workspace/train.pid 2>/dev/null) > /dev/null && echo running || echo done" 2>&1)
  if echo "$STATUS" | grep -q "done"; then
    echo "  [$i/60] 训练进程已退出"
    break
  fi
  echo "  [$i/60] 仍在跑 ($(date +%H:%M:%S))..."
done
echo ""
echo "=== train.log tail ==="
$SSH_PASS_OPT ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  root@"$IP" "tail -30 /workspace/train.log"

step "7/7 scp 回传 adapter + gguf"
mkdir -p "$ASSETS_DIR/trained"
$SSH_PASS_OPT scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  -r root@"$IP":/workspace/qwen3guard-finetuned/adapter \
  "$ASSETS_DIR/trained/"
if [ -d "$ASSETS_DIR/trained/adapter" ]; then
  echo "✅ adapter 拉回"
fi
$SSH_PASS_OPT scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
  root@"$IP":/workspace/qwen3guard-finetuned/merged-q4.gguf \
  "$ASSETS_DIR/trained/" 2>/dev/null || \
  echo "  (merged-q4.gguf 没生成,只回传 adapter)"

step "8/8 销毁实例"
"$HERE/aliyun_train_cn.sh" destroy

step "7/7 销毁实例"
"$HERE/aliyun_train_cn.sh" destroy

echo ""
echo "=========================================="
echo "✅ 端到端完成"
echo "  adapter: $ASSETS_DIR/trained/adapter/"
echo "  gguf:    $ASSETS_DIR/trained/merged-q4.gguf"
echo ""
echo "下一步本地部署:"
echo "  python deploy_local_guard.py \\"
echo "    --model $ASSETS_DIR/trained/merged-q4.gguf \\"
echo "    --port 8912 --patch-jev"
echo "=========================================="