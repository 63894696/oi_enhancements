#!/bin/bash
# aliyun_train_cn.sh — cn-shanghai GPU 训练平台启动脚本(2026-09-22)
#
# 方案 E 实施:
#   - Region: cn-shanghai(B 类区,GPU 全,价格比 HK 便宜 5-10%)
#   - 实例: ecs.gn5i-c2g1.2xlarge(8 vCPU / 32GB / P4 8GB)¥8.68/h
#   - GFW 兜底:UserData 自动配 pip aliyun mirror + HF_ENDPOINT=hf-mirror.com
#     + modelscope SDK;**本机已下好模型权重,实例内不再下载**
#   - 网络资源:新建 SG + VSW + EIP(老 3.0.50 CLI 不支持 zone-specific 资源查询)
#
# 用法:
#   ./aliyun_train_cn.sh setup    # 一次性:建 SG/VSW(可跳过,RunInstances 自动用 default)
#   ./aliyun_train_cn.sh start    # 起实例
#   ./aliyun_train_cn.sh status   # 查
#   ./aliyun_train_cn.sh ssh      # SSH 登录
#   ./aliyun_train_cn.sh stop     # 停
#   ./aliyun_train_cn.sh destroy  # 销毁
#
# 前置(用户已做):
#   - aliyun cli 在 PATH 或 C:\aliyun-cli\aliyun.exe
#   - ¥50 已充 aliyun
#   - AK 在 ~/.aliyun/config.json,RAM 有 ECS RunInstances 权限
#   - 本机已下好:Qwen3Guard-Gen-0.6B 模型权重(/workspace-local/qwen3guard/)
#                   + 训练数据 data_step1.jsonl(50 条 + 后续扩)
set -euo pipefail

ALIYUN="${ALIYUN:-/c/aliyun-cli/aliyun.exe}"
REGION="cn-shanghai"
ZONE="cn-shanghai-l"        # gn5i P4 主推 zone,无库存时备选 m/k/n
INSTANCE_TYPE="ecs.gn5i-c2g1.2xlarge"
INSTANCE_NAME="prisirtrain-step1-cn-guard"
PASSWORD="${PRISIR_TRAIN_PASS:-PrisirTrain2026!}"   # SSH password(公网 22)
SSH_PASS_OPT=""
if command -v sshpass &> /dev/null; then
  SSH_PASS_OPT="sshpass -p $PASSWORD"
fi

# 镜像:Ubuntu 22.04 LTS + 预装 NVIDIA 驱动 + CUDA(cn-shanghai 已验证 ID)
# 省 UserData 装驱动步骤,实测 100GB 系统盘够用
BASE_IMAGE_ID="ubuntu_22_04_x64_100G_with_gpu_driver_and_cuda_alibase_20260520.vhd"

# UserData:实例首次启动自动跑,装 venv + pip 配 mirror + 装 transformers/peft/bitsandbytes
# 关键:HF_ENDPOINT 让 HuggingFace 走镜像;pip 走 aliyun;不下模型(本机已下好)
USERDATA='#!/bin/bash
set -e
exec > /var/log/prisirt-init.log 2>&1
echo "=== prisirt init start $(date) ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-pip python3-venv git wget openssh-server

# NVIDIA 驱动 + CUDA(aliyun 镜像通常已预装;无则装)
if ! command -v nvidia-smi &> /dev/null; then
  apt-get install -y nvidia-driver-535
fi

# 训练 venv
python3 -m venv /opt/prisirt-venv
source /opt/prisirt-venv/bin/activate
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set global.trusted-host mirrors.aliyun.com
pip install --upgrade pip wheel setuptools

# 关键:让 HF 走镜像,modelscope 镜像备用
cat > /etc/profile.d/prisirt.sh <<EOF2
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_ENABLE_HF_TRANSFER=0
export TRANSFORMERS_OFFLINE=0
export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
export PATH=/opt/prisirt-venv/bin:\$PATH
EOF2
source /etc/profile.d/prisirt.sh

# PyTorch CUDA 12.4 + 训练套件(aliyun 镜像有时含 GPU 版 PyTorch,要重装)
pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cu124 -q
pip install transformers peft trl datasets accelerate \
  bitsandbytes sentencepiece protobuf -q

# SSH 允许密码登录(aliyun 镜像默认禁,自启动要开)
sed -i "s/^#PasswordAuthentication yes/PasswordAuthentication yes/" /etc/ssh/sshd_config
sed -i "s/^PasswordAuthentication no/PasswordAuthentication yes/" /etc/ssh/sshd_config
systemctl restart sshd 2>/dev/null || service ssh restart

nvidia-smi >> /var/log/prisirt-init.log
echo "=== prisirt init end $(date) ==="
'

run_aliyun() {
  if [ -x "$ALIYUN" ]; then
    "$ALIYUN" "$@"
  elif command -v aliyun &> /dev/null; then
    aliyun "$@"
  else
    echo "ERROR: aliyun cli not found" >&2
    exit 1
  fi
}

do_start() {
  echo ">>> Starting cn-shanghai P4: $INSTANCE_TYPE"
  # 老 CLI 3.0.50 不支持 --InstanceUserData,改为 SSH 上去跑 init 脚本
  run_aliyun ecs RunInstances \
    --RegionId "$REGION" \
    --ImageId "$BASE_IMAGE_ID" \
    --InstanceType "$INSTANCE_TYPE" \
    --InternetChargeType PayByTraffic \
    --InternetMaxBandwidthOut 10 \
    --SystemDisk.Category cloud_essd \
    --SystemDisk.Size 200 \
    --ZoneId "$ZONE" \
    --InstanceName "$INSTANCE_NAME" \
    --HostName "prisirt-cn" \
    --Password "$PASSWORD" \
    --Amount 1
  echo ""
  echo "✅ Started. Wait ~2 min for RUNNING. Then:"
  echo "   1) ./aliyun_train_cn.sh init    # SSH 上跑 init 脚本(装环境,5-10 min)"
  echo "   2) ./aliyun_train_cn.sh ssh     # SSH 登录"
  echo "   如果库存空返 InventoryNotEnough,改 ZONE 环境变量换 zone 重试"
}

# 初始化:SSH 上去跑 UserData 等价物
do_init() {
  local ip
  ip=$(run_aliyun ecs DescribeInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | python -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    inst = d['Instances']['Instance'][0]
    print(inst['NetworkAttributes']['PublicIpAddress']['IpAddress'][0])
except: pass
")
  if [ -z "$ip" ]; then
    echo "❌ 实例没 RUNNING,先 ./aliyun_train_cn.sh status 看"
    exit 1
  fi
  echo ">>> 初始化 $ip(装 venv + pip mirror + 训练套件)"
  # 用 base64 传 init.sh,绕过 heredoc EOF 嵌套
  local ud_b64
  ud_b64=$(printf '%s' "$USERDATA" | base64 -w0)
  $SSH_PASS_OPT ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@"$ip" "mkdir -p /opt/prisirt-init && echo '$ud_b64' | base64 -d > /opt/prisirt-init/init.sh && chmod +x /opt/prisirt-init/init.sh && nohup /opt/prisirt-init/init.sh > /var/log/prisirt-init.log 2>&1 & echo \$! > /tmp/init.pid && sleep 1 && echo '✅ init started pid='\$(cat /tmp/init.pid)"
  echo ""
  echo "✅ 初始化后台启动。等 5-10 min 后:"
  echo "   ./aliyun_train_cn.sh initlog    # 跟 init log"
  echo "   验证装好: ./aliyun_train_cn.sh ssh 然后 nvidia-smi"
}

do_initlog() {
  local ip
  ip=$(run_aliyun ecs DescribeInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | python -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    inst = d['Instances']['Instance'][0]
    print(inst['NetworkAttributes']['PublicIpAddress']['IpAddress'][0])
except: pass
")
  if [ -z "$ip" ]; then echo "❌ 没 RUNNING"; exit 1; fi
  $SSH_PASS_OPT ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@"$ip" "tail -50 /var/log/prisirt-init.log; echo '---'; echo 'init still running?'; ps -p \$(cat /tmp/init.pid 2>/dev/null) > /dev/null && echo YES || echo NO"
}

do_status() {
  run_aliyun ecs DescribeInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | python -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for inst in d.get('Instances', {}).get('Instance', []):
        attrs = inst.get('NetworkAttributes', {})
        ips = attrs.get('PublicIpAddress', {}).get('IpAddress', [])
        ip = ips[0] if ips else '(no public ip)'
        print(f\"  {inst.get('InstanceId')}  status={inst.get('Status')}  pub_ip={ip}  zone={inst.get('ZoneId')}\")
except Exception as e:
    print('parse:', e)
"
}

do_ssh() {
  local ip
  ip=$(run_aliyun ecs DescribeInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | python -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    inst = d['Instances']['Instance'][0]
    print(inst['NetworkAttributes']['PublicIpAddress']['IpAddress'][0])
except: pass
")
  if [ -z "$ip" ]; then echo "❌ 没 RUNNING"; exit 1; fi
  echo ">>> SSHing to root@$ip (password: $PASSWORD)"
  $SSH_PASS_OPT ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null root@"$ip" || \
    echo "(sshpass 未装;手动 ssh root@$ip 输密码)"
}

do_stop() {
  echo ">>> Stopping"
  run_aliyun ecs StopInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | tail -3
}

do_destroy() {
  echo ">>> Destroying"
  run_aliyun ecs DeleteInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" --Force true 2>&1 | tail -3
}

cmd="${1:-help}"
case "$cmd" in
  start)   do_start ;;
  init)    do_init ;;
  initlog) do_initlog ;;
  status)  do_status ;;
  ssh)     do_ssh ;;
  stop)    do_stop ;;
  destroy) do_destroy ;;
  help|*) cat <<EOF
方案 E:cn-shanghai P4 ¥8.68/h

用法:
  ./aliyun_train_cn.sh start        # 起实例(默认 zone-l)
  ./aliyun_train_cn.sh status       # 查状态
  ./aliyun_train_cn.sh ssh          # SSH 登录
  ./aliyun_train_cn.sh stop         # 停(释放 compute 费,留存储)
  ./aliyun_train_cn.sh destroy      # 销毁(释放 EIP + 数据)

环境变量:
  PRISIR_TRAIN_PASS  SSH 密码(默认:PrisirTrain2026!)
  ZONE               默认 cn-shanghai-l(无库存换 m/k/n)
EOF
    ;;
esac