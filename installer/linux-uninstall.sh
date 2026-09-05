#!/usr/bin/env bash
# PrisirAI Linux 卸载脚本
#
# 默认行为:停服务 + 删 systemd + 删 .desktop + 删 GTK 图标 + 删用户数据
#   不删安装目录(留档供备份),改 REMOVE_INSTALL_DIR=1 才会删
#   不删用户索引库(prisiraiclass.db / fcontent.db),改 REMOVE_DATA=1 才会删
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/prisirai}"
REMOVE_INSTALL_DIR="${REMOVE_INSTALL_DIR:-0}"
REMOVE_DATA="${REMOVE_DATA:-0}"

echo "=== PrisirAI Linux 卸载 ==="
echo "INSTALL_DIR       = $INSTALL_DIR"
echo "REMOVE_INSTALL_DIR= $REMOVE_INSTALL_DIR(改 =1 才会删)"
echo "REMOVE_DATA       = $REMOVE_DATA(改 =1 才会删)"

# ---------- 停服务 ----------
echo "[1/5] 停 systemd user services..."
systemctl --user disable --now prisiragent-shell.service 2>/dev/null || true
systemctl --user disable --now prisiragent-web.service   2>/dev/null || true
rm -f "$HOME/.config/systemd/user/prisiragent-web.service"
rm -f "$HOME/.config/systemd/user/prisiragent-shell.service"
systemctl --user daemon-reload
echo "  services 停 + 卸"

# ---------- 删 .desktop ----------
echo "[2/5] 删 .desktop entry..."
rm -f "$HOME/.local/share/applications/PrisirAI.desktop"
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
fi
echo "  PrisirAI.desktop 已删"

# ---------- 删 GTK theme 图标 ----------
echo "[3/5] 删 GTK theme 图标..."
for sz in 16 22 24 32 48 64 128 256; do
    rm -f "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps/prisiraiclass.png"
    rm -f "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps/PrisirAI.png"
    rm -f "$HOME/.local/share/icons/hicolor/${sz}x${sz}/apps/prisirai.png"
done
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" 2>/dev/null || true
fi
echo "  prisiraiclass/PrisirAI/prisirai 图标已删"

# ---------- 删安装目录 ----------
echo "[4/5] 安装目录 $INSTALL_DIR..."
if [[ "$REMOVE_INSTALL_DIR" == "1" && -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    echo "  安装目录已删"
else
    echo "  保留(REMOVE_INSTALL_DIR=1 才删)"
fi

# ---------- 删用户数据 ----------
echo "[5/5] 用户数据 ~/.local/share/prisir ..."
if [[ "$REMOVE_DATA" == "1" ]]; then
    rm -rf "$HOME/.local/share/prisir"
    rm -rf "$HOME/.config/PrisirAI" "$HOME/.config/prisiragent-shell"
    echo "  已删"
else
    echo "  保留(REMOVE_DATA=1 才删) — 含 findex.db / fcontent.db / 聊天记录"
fi

echo ""
echo "=== 卸载完成 ==="
echo "系统级 Rust toolchain / Python deps 没自动删(共享给其他项目),如要清理:"
echo "  rustup self uninstall"
echo "  pip uninstall pypdf rapidocr_onnxruntime opencv-python-headless Pillow"
