#!/bin/bash
# aliyun_train_sg.sh — ap-southeast-1 (新加坡) T4 训练平台启动脚本(2026-09-22)
#
# 关键变更(从 cn-shanghai P4 → ap-southeast-1 T4):
#   - P4 全 aliyun 停售(cn-shanghai/beijing/hangzhou/hongkong 全 zone 无货)
#   - T4 8GB ¥4.5/h 比 P4 ¥8.68/h 还便宜近一半
#   - ap-southeast-1a 海外 region 无 GFW 限制,直连 huggingface
#   - 网络:vpc-t4nfydowugn4ddbb8xf8y / vsw-t4n5wd75sy7ge9fr0ih6x / sg-t4nip0kwecxamntjkvsy
#
# 用法:
#   ./aliyun_train_sg.sh start    # 起实例(已建过网络,直接 RunInstances)
#   ./aliyun_train_sg.sh status   # 查
#   ./aliyun_train_sg.sh init     # SSH 上跑 init 脚本(装 venv + 训练套件)
#   ./aliyun_train_sg.sh ssh      # SSH 登录
#   ./aliyun_train_sg.sh destroy  # 销毁
set -euo pipefail

ALIYUN="${ALIYUN:-/c/aliyun-cli/aliyun.exe}"
REGION="ap-southeast-1"
ZONE="ap-southeast-1a"
INSTANCE_TYPE="ecs.gn6i-c8g1.2xlarge"   # T4 8GB
INSTANCE_NAME="prisirt-train-step2"
VPC_ID="vpc-t4nfydowugn4ddbb8xf8y"
VSW_ID="vsw-t4n5wd75sy7ge9fr0ih6x"
SG_ID="sg-t4nip0kwecxamntjkvsy"
PASSWORD="${PRISIR_TRAIN_PASS:-PrisirTrain2026!}"
SSH_PASS_OPT=""
if command -v sshpass &> /dev/null; then
  SSH_PASS_OPT="sshpass -p $PASSWORD"
fi

# 镜像:Ubuntu 22.04 LTS + 预装 NVIDIA 驱动 + CUDA
BASE_IMAGE_ID="ubuntu_22_04_x64_100G_with_gpu_driver_and_cuda_alibase_20260520.vhd"

# Init: 装 venv + pip mirror + 训练套件
# 注意:ap-southeast-1 在海外,hf-mirror 仍可用,aliyun pypi mirror 走新加坡节点
USERDATA='#!/bin/bash
set -e
exec > /var/log/prisirt-init.log 2>&1
echo "=== prisirt init start $(date) ==="

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-pip python3-venv git wget openssh-server

if ! command -v nvidia-smi &> /dev/null; then
  apt-get install -y nvidia-driver-535
fi

python3 -m venv /opt/prisirt-venv
source /opt/prisirt-venv/bin/activate
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip config set global.trusted-host mirrors.aliyun.com
pip install --upgrade pip wheel setuptools

# 镜像配置(SG 无 GFW 限制,但配镜像仍是好习惯)
cat > /etc/profile.d/prisirt.sh <<EOF2
export HF_ENDPOINT=https://hf-mirror.com
export HF_HUB_ENABLE_HF_TRANSFER=0
export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
export PATH=/opt/prisirt-venv/bin:\$PATH
EOF2
source /etc/profile.d/prisirt.sh

pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cu124 -q
pip install transformers peft trl datasets accelerate \
  bitsandbytes sentencepiece protobuf -q

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

get_pub_ip() {
  run_aliyun ecs DescribeInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | python -c "
import json, sys
try:
    d = json.loads(sys.stdin.read())
    inst = d['Instances']['Instance'][0]
    print(inst['NetworkAttributes']['PublicIpAddress']['IpAddress'][0])
except Exception:
    pass
"
}

do_start() {
  echo ">>> Starting ap-southeast-1 T4: $INSTANCE_TYPE"
  run_aliyun ecs RunInstances \
    --RegionId "$REGION" \
    --ImageId "$BASE_IMAGE_ID" \
    --InstanceType "$INSTANCE_TYPE" \
    --SecurityGroupId "$SG_ID" \
    --VSwitchId "$VSW_ID" \
    --InternetChargeType PayByTraffic \
    --InternetMaxBandwidthOut 10 \
    --SystemDisk.Category cloud_essd \
    --SystemDisk.Size 200 \
    --ZoneId "$ZONE" \
    --InstanceName "$INSTANCE_NAME" \
    --HostName "prisirt-sg" \
    --Password "$PASSWORD" \
    --Amount 1
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
        print(f\"  {inst.get('InstanceId')}  status={inst.get('Status')}  pub_ip={ip}  zone={inst.get('ZoneId')}  type={inst.get('InstanceType')}\")
except Exception as e:
    print('parse:', e)
"
}

do_init() {
  local ip
  ip=$(get_pub_ip)
  if [ -z "$ip" ]; then
    echo "❌ 实例没 RUNNING,先 ./aliyun_train_sg.sh status 看"
    exit 1
  fi
  echo ">>> 初始化 $ip"
  local ud_b64
  ud_b64=$(printf '%s' "$USERDATA" | base64 -w0)
  $SSH_PASS_OPT ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@"$ip" "mkdir -p /opt/prisirt-init && echo '$ud_b64' | base64 -d > /opt/prisirt-init/init.sh && chmod +x /opt/prisirt-init/init.sh && nohup /opt/prisirt-init/init.sh > /var/log/prisirt-init.log 2>&1 & echo \$! > /tmp/init.pid && sleep 1 && echo '✅ init started pid='\$(cat /tmp/init.pid)"
  echo ""
  echo "✅ 初始化后台启动。~5-10 min 后 ./aliyun_train_sg.sh initlog"
}

do_initlog() {
  local ip
  ip=$(get_pub_ip)
  if [ -z "$ip" ]; then echo "❌ 没 RUNNING"; exit 1; fi
  $SSH_PASS_OPT ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
    root@"$ip" "tail -30 /var/log/prisirt-init.log; echo '---'; echo 'init still running?'; ps -p \$(cat /tmp/init.pid 2>/dev/null) > /dev/null && echo YES || echo NO"
}

do_ssh() {
  local ip
  ip=$(get_pub_ip)
  if [ -z "$ip" ]; then echo "❌ 没 RUNNING"; exit 1; fi
  echo ">>> SSHing to root@$ip"
  $SSH_PASS_OPT ssh -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null root@"$ip"
}

do_destroy() {
  echo ">>> Destroying instances named $INSTANCE_NAME"
  run_aliyun ecs DescribeInstances --RegionId "$REGION" \
    --InstanceName "$INSTANCE_NAME" 2>&1 | python -c "
import json, sys
try:
    d = json.load(sys.stdin)
    for inst in d.get('Instances', {}).get('Instance', []):
        iid = inst.get('InstanceId', '')
        print(iid)
except: pass
" | while read iid; do
    [ -z "$iid" ] && continue
    echo "  deleting $iid ..."
    run_aliyun ecs DeleteInstances --RegionId "$REGION" \
      --InstanceId.N "1" --InstanceId.1 "$iid" --Force true 2>&1 | tail -2
  done
}

cmd="${1:-help}"
case "$cmd" in
  start)   do_start ;;
  status)  do_status ;;
  init)    do_init ;;
  initlog) do_initlog ;;
  ssh)     do_ssh ;;
  destroy) do_destroy ;;
  help|*) cat <<EOF
方案调整(2026-09-22):ap-southeast-1a T4 8GB ¥4.5/h
  P4 全 aliyun 停售,改 T4 海外 region

用法:
  ./aliyun_train_sg.sh start    # 起实例
  ./aliyun_train_sg.sh status   # 查
  ./aliyun_train_sg.sh init     # 初始化(SSH 装环境,~5-10 min)
  ./aliyun_train_sg.sh initlog  # 看 init log
  ./aliyun_train_sg.sh ssh      # SSH 登录
  ./aliyun_train_sg.sh destroy  # 销毁

资源(已建好):
  VPC  vpc-t4nfydowugn4ddbb8xf8y
  VSW  vsw-t4n5wd75sy7ge9fr0ih6x (ap-southeast-1a)
  SG   sg-t4nip0kwecxamntjkvsy
EOF
    ;;
esac