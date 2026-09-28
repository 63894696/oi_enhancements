#!/bin/bash
# aliyun_train.sh — Aliyun GPU 实例训练平台启动脚本(2026-09-22)
#
# 目的:
#   - 复用 handbook 8-07 验证过的 aliyuncli + cn-shanghai + sg/vsw 模式
#   - GPU 实例起停按用量计费,训练完 stop 实例不收费
#   - 用户只需要事先: 浏览器登录 RAM 给 AccessKey ECS RunInstances 权限
#
# 用法:
#   ./aliyun_train.sh start  [step1|step2|step3]   # 起实例
#   ./aliyun_train.sh status                       # 查实例状态
#   ./aliyun_train.sh ssh     [step1|step2|step3]  # SSH 登录
#   ./aliyun_train.sh stop                        # 停止实例(释放不收费)
#   ./aliyun_train.sh destroy                     # 销毁实例 + 释放 EIP
#
# 前置:
#   - aliyun cli 在 PATH(或在 C:\aliyun-cli\aliyun.exe)
#   - ~/.aliyun/config.json 已配 AK + cn-hongkong region
#   - 已在 RAM 控制台给该 AK 授权:ecs:RunInstances / ecs:DescribeInstances /
#     ecs:StopInstances / ecs:StartInstances / ecs:DescribeImages /
#     vpc:DescribeVSwitches / ecs:DescribeSecurityGroups / ecs:AllocatePublicIpAddress
#   - 已 cn-shanghai 创建 VSwitch + SecurityGroup(开放 22 端口)
#
# 关键决策(2026-09-22):
#   - Region 选 cn-shanghai:比 cn-hongkong 便宜 ~5%,且无 GFW 出口限制(手册 8-07 验证)
#   - 系统盘 cloud_essd + 200GB:训练数据集 + 模型权重足够
#   - 公网带宽 PayByTraffic 10Mbps:避免按带宽固定计费空跑
#   - 用基础 Linux 镜像(不依赖 marketplace 预装):UserData 一次性装 CUDA + PyTorch

set -euo pipefail

# ---- 配置 ----
ALIYUN="${ALIYUN:-/c/aliyun-cli/aliyun.exe}"
# 必须用 cn-hongkong:国内区 GFW 阻 github/huggingface,无法拉训练代码+模型权重
# (handbook 8-07 验证:chromium.googlesource.com 在 HK ECS 出站可达)
REGION="cn-hongkong"
# 复用 8 月 chromium 编译用的网络(secbrowser-vpc)
SECURITY_GROUP_ID="${SECURITY_GROUP_ID:-sg-j6c5xtnlr1v54naric40}"   # secbrowser-sg
VSWITCH_ID="${VSWITCH_ID:-vsw-j6chjrio5ijum3ynw1hxl}"               # secbrowser-vsw-d
ZONE_ID="${ZONE_ID:-cn-hongkong-d}"                                 # 跟 VSwitch 同 zone

# 实例规格(按 step 选择)
declare -A SPECS=(
  ["step1"]="ecs.gn7-c8g1.2xlarge"   # T4 16GB  ¥3.5/h  护栏兜底
  ["step2"]="ecs.gn6v-c8g1.2xlarge"  # V100 16GB ¥10/h   意图分类
  ["step3"]="ecs.gn6v-c8g1.2xlarge"  # V100 16GB ¥10/h   LLM 兜底
)

declare -A INSTANCE_NAMES=(
  ["step1"]="prisirtrain-step1-guard"
  ["step2"]="prisirtrain-step2-intent"
  ["step3"]="prisirtrain-step3-llm"
)

# 基础镜像:Ubuntu 22.04 LTS(公网 ImageId 在 cn-hongkong region)
# 用 system public 镜像族,用户启动后脚本装 CUDA+PyTorch
# ImageId 已通过 DescribeImages 查到(2026-09-22)
BASE_IMAGE_ID="ubuntu_22_04_x64_20G_alibase_20260916.vhd"

# UserData 启动脚本:实例首次启动时自动跑
# 一键装 CUDA Toolkit 12.4 + Python 3.11 + PyTorch nightly(GPU)
USERDATA='#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3-pip python3-venv git wget openssh-client

# NVIDIA 驱动 + CUDA(aliyun 镜像通常已预装 nvidia-driver;若没装)
if ! command -v nvidia-smi &> /dev/null; then
  apt-get install -y nvidia-driver-535
fi

# 创建训练 venv
python3 -m venv /opt/prisirt-venv
/opt/prisirt-venv/bin/pip install --upgrade pip wheel setuptools
/opt/prisirt-venv/bin/pip install torch torchvision torchaudio \
  --index-url https://download.pytorch.org/whl/cu124
/opt/prisirt-venv/bin/pip install transformers peft trl datasets \
  accelerate bitsandbytes sentencepiece protobuf
echo "===Train env ready===" >> /var/log/prisirt-init.log
nvidia-smi >> /var/log/prisirt-init.log
'

# ---- 实现 ----
cmd="${1:-help}"
step="${2:-}"

run_aliyun() {
  if [ -x "$ALIYUN" ]; then
    "$ALIYUN" "$@"
  elif command -v aliyun &> /dev/null; then
    aliyun "$@"
  else
    echo "ERROR: aliyun cli not found at $ALIYUN and not in PATH" >&2
    exit 1
  fi
}

do_start() {
  local step="$1"
  local spec="${SPECS[$step]:-}"
  local name="${INSTANCE_NAMES[$step]:-}"
  if [ -z "$spec" ]; then
    echo "ERROR: unknown step '$step'. Expected: step1|step2|step3"
    exit 1
  fi
  echo ">>> Starting $step: spec=$spec name=$name"
  # 带换行的 UserData 必须用 --InstanceUserData 后跟 base64 编码内容
  local ud_b64
  ud_b64=$(printf '%s' "$USERDATA" | base64 -w0)
  run_aliyun ecs RunInstances \
    --RegionId "$REGION" \
    --ImageId "$BASE_IMAGE_ID" \
    --InstanceType "$spec" \
    --SecurityGroupId "$SECURITY_GROUP_ID" \
    --VSwitchId "$VSWITCH_ID" \
    --InternetChargeType PayByTraffic \
    --InternetMaxBandwidthOut 10 \
    --SystemDisk.Category cloud_essd \
    --SystemDisk.Size 200 \
    --ZoneId "$ZONE_ID" \
    --InstanceName "$name" \
    --HostName "$name" \
    --InstanceUserData "$ud_b64" \
    --Amount 1
  echo ""
  echo "✅ Started. Wait ~2 min for instance RUNNING, then run: ./aliyun_train.sh ssh $step"
}

do_status() {
  for s in "${!INSTANCE_NAMES[@]}"; do
    local name="${INSTANCE_NAMES[$s]}"
    echo "=== $s ($name) ==="
    run_aliyun ecs DescribeInstances \
      --RegionId "$REGION" \
      --InstanceName "$name" 2>&1 | python -c "
import json, sys
try:
    d = json.load(sys.stdin)
    instances = d.get('Instances', {}).get('Instance', [])
    for inst in instances:
        attrs = inst.get('NetworkAttributes', {})
        ips = attrs.get('PublicIpAddress', {}).get('IpAddress', [])
        ip = ips[0] if ips else '(no public ip)'
        print(f\"  {inst.get('InstanceId')}  status={inst.get('Status')}  pub_ip={ip}  type={inst.get('InstanceType',{}).get('InstanceType')}\")
except Exception as e:
    print('parse:', e)
"
  done
}

do_ssh() {
  local step="$1"
  local name="${INSTANCE_NAMES[$step]:-}"
  if [ -z "$name" ]; then echo "ERROR: unknown step"; exit 1; fi
  local ip
  ip=$(run_aliyun ecs DescribeInstances --RegionId "$REGION" --InstanceName "$name" 2>&1 \
    | python -c "
import json, sys
d = json.load(sys.stdin)
inst = d['Instances']['Instance'][0]
print(inst['NetworkAttributes']['PublicIpAddress']['IpAddress'][0])
")
  echo ">>> SSHing to root@$ip"
  ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null root@"$ip"
}

do_stop() {
  echo ">>> Stopping all train instances"
  for s in "${!INSTANCE_NAMES[@]}"; do
    local name="${INSTANCE_NAMES[$s]}"
    run_aliyun ecs StopInstances \
      --RegionId "$REGION" \
      --InstanceName "$name" 2>&1 | tail -3
  done
  echo "✅ Stopped. Instances preserved (no compute charges, but storage still costs)."
}

do_destroy() {
  echo ">>> Destroying all train instances"
  for s in "${!INSTANCE_NAMES[@]}"; do
    local name="${INSTANCE_NAMES[$s]}"
    run_aliyun ecs DeleteInstances \
      --RegionId "$REGION" \
      --InstanceName "$name" --Force true 2>&1 | tail -3
  done
  echo "✅ Destroyed."
}

case "$cmd" in
  start)  do_start "$step" ;;
  status) do_status ;;
  ssh)    do_ssh "$step" ;;
  stop)   do_stop ;;
  destroy) do_destroy ;;
  help|*) cat <<EOF
用法:
  ./aliyun_train.sh start  step1|step2|step3   # 起训练实例
  ./aliyun_train.sh status                       # 查所有训练实例状态
  ./aliyun_train.sh ssh    step1|step2|step3    # SSH 登录
  ./aliyun_train.sh stop                         # 停实例(保留数据)
  ./aliyun_train.sh destroy                      # 销毁实例(释放 EIP + 数据)

环境变量:
  SECURITY_GROUP_ID (default: sg-xxxxxxxxx)
  VSWITCH_ID        (default: vsw-xxxxxxxxx)
  ZONE_ID           (default: cn-shanghai-b)
  ALIYUN            (default: /c/aliyun-cli/aliyun.exe)

前置:
  1. 在 cn-shanghai 创建 VSwitch + SecurityGroup(开放 22)
  2. 在 RAM 控制台给 AK 授权 ECS RunInstances / Describe / Stop / Start / Delete
     + VPC DescribeVSwitches / SecurityGroups
  4. 把 SECURITY_GROUP_ID 和 VSWITCH_ID 写到本脚本默认或通过 env 传入
EOF
    ;;
esac