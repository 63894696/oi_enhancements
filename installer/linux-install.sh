#!/usr/bin/env bash
# PrisirAI Linux 一键安装脚本(Debian 13 / Ubuntu 24+ 验证)
#
# 自动化任务:#7 PrisirAI Linux 安装包
# 用法:
#   bash linux-install.sh                  # 默认安装到 ~/prisiraiclass
#   INSTALL_DIR=/opt/prisirai bash ...     # 自定义安装目录
#   WITH_RUST=0 bash ...                   # 跳过 Rust toolchain(本机已有)
#   WITH_INDEX=1 bash ...                  # 安装后自动建 findex/fcontent 索引
#   ACCEPT_LICENSE=1 bash ...              # 非交互环境下跳过协议确认(默认会问)
set -euo pipefail

# ---------- 参数 ----------
INSTALL_DIR="${INSTALL_DIR:-$HOME/prisirai}"
WITH_RUST="${WITH_RUST:-1}"        # 第一次装 = 1,本机已有 rust 可设 0 跳过
WITH_INDEX="${WITH_INDEX:-0}"      # 默认不自动建索引(用户首次开探囊时再触发)
SKIP_DEPS="${SKIP_DEPS:-0}"        # 跳过 apt install(完全离线环境用)
SKIP_FIREJAIL="${SKIP_FIREJAIL:-1}" # electron 跑 firejail 沙箱(默认关,sandbox 已在 electron 层)
ACCEPT_LICENSE="${ACCEPT_LICENSE:-0}" # 默认要求用户在终端确认 OIE-PCS-1.0

echo "=== PrisirAI Linux 安装 ==="
echo "INSTALL_DIR     = $INSTALL_DIR"
echo "WITH_RUST       = $WITH_RUST"
echo "WITH_INDEX      = $WITH_INDEX"
echo "ACCEPT_LICENSE  = $ACCEPT_LICENSE (1=跳过终端确认)"

# ---------- 0. 协议确认 (OIE-PCS-1.0) ----------
# Win 端 NSIS 通过 MUI_PAGE_LICENSE 弹 EULA 页;Linux 端在终端里展示摘要,
# 链接到 LICENSE 全文。ACCEPT_LICENSE=1 用于 CI/无人值守/容器场景。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LICENSE_FILE="$SCRIPT_DIR/LICENSE.txt"

if [[ "$ACCEPT_LICENSE" != "1" ]]; then
    echo ""
    echo "================================================================"
    echo "  OI Enhancements Personal and Commercial Source License v1.0"
    echo "  (OIE-PCS-1.0) — Installation Notice"
    echo "================================================================"
    if [[ -f "$LICENSE_FILE" ]]; then
        # 展示 LICENSE.txt(摘要 + 双语);非交互式TTY下 cat 全文即可
        cat "$LICENSE_FILE"
    else
        echo "WARN: 找不到 $LICENSE_FILE,只显示简短摘要。"
        echo ""
        echo "SPDX-License-Identifier: LicenseRef-OI-Enhancements-PCS-1.0"
        echo ""
        echo "1. 个人非商业使用需遵守 OIE-PCS-1.0。"
        echo "2. 商业使用需另行取得商业许可(COMMERCIAL-LICENSE.md)。"
        echo "3. 修改 Core Components(CORE-COMPONENTS.md 列出的路径)且"
        echo "   分发 / 作为网络服务时,必须按 OIE-PCS-1.0 公开。"
        echo "4. 品牌商标(Prisir AI / oiagent / 火焰标识 / assets/ 图标)"
        echo "   不通过 OIE-PCS-1.0 授权,商业使用需品牌许可。"
        echo "5. 适用法律:香港特别行政区法律;争议由 HKIAC 仲裁。"
    fi
    echo ""
    echo "完整许可文本:"
    echo "  $LICENSE_FILE"
    echo "  https://github.com/63894696/oi_enhancements/blob/master/LICENSE"
    echo "================================================================"
    if [[ -t 0 ]]; then
        # 交互式TTY:要求输入 yes
        read -r -p "接受 OIE-PCS-1.0 条款并继续安装? [yes/no]: " reply
        case "$reply" in
            yes|y|Y|Yes|YES) echo "  已接受 OIE-PCS-1.0,继续..." ;;
            *) echo "ERR: 未接受协议,退出安装。" >&2; exit 1 ;;
        esac
    else
        echo "ERR: 非交互式TTY但 ACCEPT_LICENSE!=1。设置 ACCEPT_LICENSE=1 后重跑。" >&2
        exit 1
    fi
fi

# ---------- 前置检查 ----------
if [[ "$(uname -s)" != "Linux" ]]; then
    echo "ERR: 仅 Linux 支持,当前 $(uname -s)" >&2; exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERR: python3 未装,Debian/Ubuntu: sudo apt install -y python3 python3-pip" >&2; exit 1
fi
if ! command -v node >/dev/null 2>&1; then
    echo "ERR: node 未装(electron 需要)" >&2; exit 1
fi

# ---------- 架构检查:仅支持 x86_64 ----------
# 2026-08-28 决策:本装包仅支持 x86_64 Linux(标准服务器 / 桌面 / WSL)。
# ARM64(树莓派 4B+ / AWS Graviton / Ampere)与 RISC-V 未提供独立装包。
# ARM64 用户需: cargo build --target aarch64-unknown-linux-gnu +
#  Electron arm64 binary + onnxruntime ARM wheel,均未实测,可能跑不通。
ARCH="$(uname -m)"
if [[ "$ARCH" != "x86_64" ]]; then
    echo "ERR: 本装包仅支持 x86_64 Linux。当前架构:$ARCH。" >&2
    echo "  ARM64 / RISC-V / 其他架构未提供独立装包。" >&2
    echo "  如确需在 $ARCH 上跑,可手动:" >&2
    echo "    1) cargo build --release --target ${ARCH}-unknown-linux-gnu" >&2
    echo "    2) 装 Electron ${ARCH} 二进制(从 electronjs.org 下载)" >&2
    echo "    3) 装 onnxruntime ${ARCH} wheel" >&2
    echo "  上述步骤未实测,失败自负。" >&2
    exit 1
fi
echo "  架构: x86_64 ✓"

# ---------- 0. 系统依赖 ----------
if [[ "$SKIP_DEPS" != "1" ]]; then
    echo "[1/9] 装系统依赖 (apt)..."
    if command -v apt-get >/dev/null 2>&1; then
        SUDO=""
        [[ $EUID -ne 0 ]] && SUDO="sudo"
        $SUDO apt-get update -qq
        $SUDO apt-get install -y --no-install-recommends \
            python3-pip python3-venv \
            libx11-6 libxcb1 libxshmfence1 libnss3 libatk-bridge2.0-0 \
            libgtk-3-0 libdrm2 libgbm1 libasound2t64 fonts-noto-cjk \
            xdg-utils
        echo "  apt 依赖装好"
    else
        echo "WARN: 不是 apt 系,跳过系统包。请手动装: libx11 libxcb libnss3 libgtk-3 fonts-noto-cjk" >&2
    fi
fi

# ---------- 1. Python 依赖 ----------
echo "[2/9] 装 Python 依赖..."
pip install --break-system-packages --quiet \
    pypdf rapidocr_onnxruntime opencv-python-headless Pillow || {
    echo "WARN: pip install 部分失败,fcontent/截图 OCR 功能可能不可用,对话主链不受影响" >&2
}

# ---------- 2. Rust toolchain (findex 引擎编译) ----------
if [[ "$WITH_RUST" == "1" ]]; then
    echo "[3/9] 装 Rust toolchain (rustup)..."
    if ! command -v cargo >/dev/null 2>&1; then
        curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
            --default-toolchain stable --profile minimal
        # shellcheck disable=SC1091
        source "$HOME/.cargo/env"
        echo "  Rust 装好: $(rustc --version)"
    else
        echo "  cargo 已有: $(cargo --version)"
    fi
else
    if ! command -v cargo >/dev/null 2>&1; then
        echo "ERR: WITH_RUST=0 但 cargo 也没装,无法编译 findex 引擎" >&2; exit 1
    fi
    # shellcheck disable=SC1091
    source "$HOME/.cargo/env" 2>/dev/null || true
fi

# ---------- 3. 部署项目文件 ----------
echo "[4/9] 部署项目文件到 $INSTALL_DIR..."
mkdir -p "$INSTALL_DIR"

# 复制仓库根的运行时文件(由调用方决定怎么塞过来;这里假设 git clone 后
# 仓库根是当前 cwd,拷 oiagent-shell/ oiagent_web.py prisir_findex/ prisir_fcontent/ assets/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 必需文件
for item in oiagent-shell oiagent_web.py prisir_findex prisir_fcontent assets; do
    if [[ -e "$REPO_ROOT/$item" ]]; then
        cp -r "$REPO_ROOT/$item" "$INSTALL_DIR/"
        echo "  复制 $item"
    else
        echo "WARN: 仓库缺 $item,跳过" >&2
    fi
done

# ---------- 4. 编译 Rust findex 引擎 ----------
echo "[5/9] 编译 prisir_findex (Rust → .so)..."
if [[ -d "$INSTALL_DIR/prisir_findex/src" ]]; then
    (cd "$INSTALL_DIR/prisir_findex" && cargo build --release 2>&1 | tail -5)
    if [[ -f "$INSTALL_DIR/prisir_findex/target/release/libprisir_findex.so" ]]; then
        echo "  libprisir_findex.so 编译好 ($(du -h "$INSTALL_DIR/prisir_findex/target/release/libprisir_findex.so" | cut -f1))"
    else
        echo "ERR: 编译失败,看上方 cargo 输出" >&2; exit 1
    fi
else
    echo "WARN: 缺 prisir_findex/src,跳过编译,findex 搜索功能不可用" >&2
fi

# ---------- 5. GTK theme 图标(xfwm4 标题栏图标) ----------
echo "[6/9] 装 GTK theme 图标..."
ICON_SRC="$INSTALL_DIR/assets/prisIr-flame-256.png"
if [[ ! -f "$ICON_SRC" ]]; then
    # 退而求其次:用 48px
    ICON_SRC=$(ls "$INSTALL_DIR/assets"/prisIr-flame-*.png 2>/dev/null | head -1)
fi
if [[ -n "$ICON_SRC" && -f "$ICON_SRC" ]]; then
    ICON_DIR_BASE="$HOME/.local/share/icons/hicolor"
    for sz in 16 22 24 32 48 64 128 256; do
        mkdir -p "$ICON_DIR_BASE/${sz}x${sz}/apps"
        # xfwm4 GTK theme lookup 查 res_class 派生的 icon-name,
        # 大小写不敏感且会 lowercase,所以 alias 三种命名兜底:
        cp "$ICON_SRC" "$ICON_DIR_BASE/${sz}x${sz}/apps/prisiraiclass.png"
        cp "$ICON_SRC" "$ICON_DIR_BASE/${sz}x${sz}/apps/PrisirAI.png"
        cp "$ICON_SRC" "$ICON_DIR_BASE/${sz}x${sz}/apps/prisirai.png"
    done
    # 刷 GTK icon cache(部分主题需要)
    if command -v gtk-update-icon-cache >/dev/null 2>&1; then
        gtk-update-icon-cache -f -t "$ICON_DIR_BASE" 2>/dev/null || true
    fi
    echo "  GTK theme 图标就位(prisiraiclass/PrisirAI/prisirai 三 alias)"
else
    echo "WARN: 在 assets/ 找不到 prisIr-flame 图标,标题栏图标可能不显示" >&2
fi

# ---------- 6. .desktop entry ----------
echo "[7/9] 装 .desktop entry..."
DESKTOP_DIR="$HOME/.local/share/applications"
mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/PrisirAI.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Prisir AI
Name[zh_CN]=湃睿思 AI
GenericName=AI Assistant
GenericName[zh_CN]=AI 助手
Comment=Prisir(湃睿思) AI — 本地对话助手 (Linux 版)
Comment[zh_CN]=Prisir(湃睿思) AI — 本地对话助手
Exec=$INSTALL_DIR/oiagent-shell/node_modules/.bin/electron $INSTALL_DIR/oiagent-shell --disable-gpu --disable-software-rasterizer --in-process-gpu %u
Icon=prisiraiclass
Terminal=false
Categories=Office;Network;
StartupNotify=true
StartupWMClass=PrisirAI
Keywords=AI;Chat;Assistant;
EOF
chmod +x "$DESKTOP_DIR/PrisirAI.desktop"
# 更新桌面数据库
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi
echo "  .desktop entry: $DESKTOP_DIR/PrisirAI.desktop"

# ---------- 7. systemd user services ----------
echo "[8/9] 装 systemd user services..."
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/oiagent-web.service" <<EOF
[Unit]
Description=PrisirAI oiagent-web (Python 后端, 127.0.0.1:18802)
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR
ExecStart=/usr/bin/python3 $INSTALL_DIR/oiagent_web.py --port 18802
Restart=on-failure
RestartSec=3
Environment=DISPLAY=:0
# 写到 journalctl --user -u oiagent-web

[Install]
WantedBy=default.target
EOF

cat > "$SYSTEMD_DIR/oiagent-shell.service" <<EOF
[Unit]
Description=PrisirAI oiagent-shell (Electron UI, 依赖 web 起)
After=oiagent-web.service
BindsTo=oiagent-web.service

[Service]
Type=simple
WorkingDirectory=$INSTALL_DIR/oiagent-shell
ExecStart=$INSTALL_DIR/oiagent-shell/node_modules/.bin/electron $INSTALL_DIR/oiagent-shell --disable-gpu --disable-software-rasterizer --in-process-gpu
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=OAUTH_AUTO_REDIRECT=1
# 写到 journalctl --user -u oiagent-shell

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable oiagent-web.service oiagent-shell.service
echo "  services 装好(默认 enable,启动用: systemctl --user start oiagent-shell)"

# ---------- 启动 ----------
echo ""
echo "=== 安装完成 ==="
echo ""
echo "下一步:"
echo "  systemctl --user start oiagent-shell   # 启动服务(开窗)"
if [[ "$WITH_INDEX" == "1" ]]; then
    echo "  WITH_INDEX=1 自动建索引在下面..."
    systemctl --user start oiagent-web
    sleep 3
    echo "  findex 扫 ~/"
    curl -s -X POST -H 'Content-Type: application/json' \
        -d "{\"roots\":[\"$HOME\"],\"exclude\":[\"$HOME/.cache\"]}" \
        http://127.0.0.1:18802/oiagent/api/findex/enable
    echo ""
    echo "  fcontent 索引 $INSTALL_DIR"
    curl -s -X POST -H 'Content-Type: application/json' \
        -d "{\"roots\":[\"$INSTALL_DIR\"],\"ocr\":false}" \
        http://127.0.0.1:18802/oiagent/api/fcontent/enable
    echo ""
else
    echo "  (跳过索引重建 — WITH_INDEX=1 可自动跑,或手动:浏览器开探囊页面触发)"
fi
echo ""
echo "日志查看:"
echo "  journalctl --user -u oiagent-shell -f"
echo "  journalctl --user -u oiagent-web  -f"
