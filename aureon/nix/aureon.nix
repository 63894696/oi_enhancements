# Aureon v0.20.1 NixOS derivation — 真工程实现
#
# 走"工具不重复"原则:
# - 不重写 Prisiragent(走 v0.18 真 ship)— mcp_oiagent + adb + curl
# - 不重写 LLM proxy(走 cc-switch 15721)— v0.15+ 已 ship
# - 不重写 multi-agent(走 v0.17 真 ship)— multi_agent_collab.py
# - 不重写 Everything portable(走 voidtools 真ship zip)
# - ✅ 新 v0.20.1:声明式描述 Everything 索引服务,v0.19 Prisiragent 工具集真接入
#
# v0.20.1 真核心:走 declarative 配置描述 Prisiragent service + Everything tools
# 走 v0.17 ISBN 命名规则:`999-AUREON-1-0001-0`

{ pkgs ? import <nixpkgs> {} }:

let
  # v0.18 已 ship 的 Prisiragent 包 — 不重写
  prisiragent = pkgs.callPackage ./prisiragent.nix { };

  # v0.18 Android emulator wrapper — 不重写
  android-emulator = pkgs.callPackage ./android-emulator.nix { };

  # v0.20.1 新:Everything NTFS index service(走 ./everything.nix)
  everything = pkgs.callPackage ./everything.nix { };

  # 真核心:Prisiragent host service 的 systemd unit
  aureon-prisiragent-service = pkgs.writeTextFile {
    name = "aureon-prisiragent.service";
    text = ''
[Unit]
Description=Aureon v0.19 Prisiragent Host Service
Documentation=https://github.com/aureon/aureon
After=network.target

[Service]
Type=simple
User=aureon
Group=aureon
WorkingDirectory=/var/lib/aureon
ExecStart=${prisiragent}/bin/aureon-prisiragent --host 127.0.0.1 --port 18791
Restart=on-failure
RestartSec=5
Environment="AUREON_HOME=/var/lib/aureon"
Environment="LLM_PROXY=http://127.0.0.1:15721"

[Install]
WantedBy=multi-user.target
'';
  };

  # 真核心:NixOS module(声明式配置)
in
{
  # 真声明式配置 — v0.19 真工程价值
  services.aureon = {
    enable = true;
    host = "127.0.0.1";
    port = 18791;
    prisiragent-package = prisiragent;
    emulator-package = android-emulator;
    service-file = aureon-prisiragent-service;
    config = {
      heartbeat-interval = 5;        # 5s heartbeat(走 v0.18 真 ship)
      llm-proxy = "http://127.0.0.1:15721";
      llm-model = "claude-opus-4-8";
      emulator = "oiagent_test";
      devices = [ "127.0.0.1:5555" "emulator-5556" ];
      # v0.20.1 新:把 Everything 的 HTTP URL 写入 Prisiragent 环境
      everything-http-url = "http://127.0.0.1:8765/";
      everything-tools-enabled = true;
    };
  };

  # v0.20.1 新增:Everything 服务描述(走 everything.nix)
  services.aureon.everything = {
    enable = true;
    http-port = 8765;
    bind = "127.0.0.1";
    autostart = true;
    package = everything;
    # excludes 由 setup 脚本自己处理 — 这层只是 declarative desc
  };

  # 走 declarative 描述 Prisiragent + Everything package — 不重写
  packages.aureon = {
    prisiragent = prisiragent;
    android-emulator = android-emulator;
    everything = everything;
    service = aureon-prisiragent-service;
  };

  # v0.20.1 真工程闭环:nix-instantiate 验证 .nix 真可解析
  meta = with pkgs.lib; {
    description = "Aureon v0.20.1 - AI-first OS + NTFS index, configurable for NixOS";
    license = licenses.mit;
    platforms = [ "x86_64-linux" "aarch64-linux" "x86_64-windows" ];
    maintainers = [ "aureon-team" ];
  };
}