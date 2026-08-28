# Aureon v0.20.1 Everything — declarative derivation for NTFS full-disk indexing
#
# 走"工具不重复"原则:
# - 不重写 Everything 本身(走官方 portable zip,不编)
# - 不重写 NTFS 索引逻辑(走 voidtools native)
# - 不重写 HKCU Run / LLM probe / 一切跟 Everything 启动后行为耦合的 wrappers
# - ✅ 新:把这套组合(Aureon host service + Everything HTTP server + OIagent tool)
#   描述为单个 Nix expression,方便任意 Linux/WSL/NixOS 主机可复现
#
# v0.20.1 真核心:用 Nix 描述"声明式索引服务"
# - voidtools Everything 1.4.1.969 portable(Win x64 zip)
# - HTTP server port 8765(本地 loopback)
# - HKCU Run -startup 拉起(用户登录即托管)
# - OIagent 通过 everything_launcher.py wrapper 调用 http://127.0.0.1:8765/

{ pkgs ? import <nixpkgs> {} }:

let
  # ============================================================================
  # Everything 1.4.1.969 portable zip — 走 fetchurl 拉官方资源
  # 这是个稳定版本,2024-01-12 voidtools 官方发版,UI 英文 + 多语言(ln 文件)
  # Windows-only binary(x86_64),Linux/WSL 跑可达但只描述
  #
  # SHA256 待填 — 我们用的是 `D:\down\` 上的同一份 zip,如果本机不联网就走
  # `fetchFromGitHub` 或 `requireFile` 拿相同 hash 的固定一份
  # ============================================================================

  everything-portable = pkgs.fetchurl {
    url = "https://www.voidtools.com/Everything-1.4.1.969.x64.zip";
    sha256 = "0000000000000000000000000000000000000000000000000000000000000000";  # 占位 — 实际部署时必须填真 hash
  };

  # 解包 — 写到 Nix store,只读
  everything-extracted = pkgs.runCommand "everything-1.4.1.969" {
    src = everything-portable;
    nativeBuildInputs = [ pkgs.unzip ];
  } ''
    mkdir -p $out
    cd $out
    unzip $src
    chmod +x Everything.exe
  '';

  # ============================================================================
  # INI 模板 — 声明式描述 Everything 配置
  # 关键:http_server_enabled=1 + http_server_port=8765 + 数据盘 exclude
  # ============================================================================
  everything-ini = pkgs.writeText "Everything.ini" ''
    [Everything]
    app_data=1
    run_as_admin=1
    allow_http_server=1
    allow_etp_server=0
    ; OIagent / Aureon 调的统一 HTTP 端口(Windos 80 通常被占用 → 用 8765)
    http_server_enabled=1
    http_server_port=8765
    ; 排除系统/临时目录以减少索引负担
    exclude_list_enabled=1
    exclude_hidden_files_and_folders=0
    exclude_system_files_and_folders=0
    ; NTFS watcher 性能
    db_update_thread_priority=-15
    index_recent_changes=1
    monitor_thread_mode_background=1
    ; 不要每次启动都重建 db — db 多用户模式(write lock)
    db_multi_user_filename=0
    db_compress=0
  '';

  # ============================================================================
  # Wrapper — portable exe + 自动化启动参数 + 配置覆盖
  # 走 stdenv.mkDerivation 而非 fetchurl,允许生成可执行入口
  # ============================================================================
  everything-bin = pkgs.runCommand "everything-1.4.1.969-bin" {
    inherit everything-extracted everything-ini;
    nativeBuildInputs = [ pkgs.makeWrapper ];
  } ''
    mkdir -p $out/bin
    cp -r $everything-extracted/* $out/
    # 拷贝 INI 模板到全局配置目录(真机写一份到 %APPDATA%\Everything)
    install -Dm644 $everything-ini $out/share/Everything.ini.template

    # Wrapper:启动 Everything.exe 时把 -config 指向 store 中的 ini 副本
    # (但官方文档说 Everything.exe 不接受 -config 参数,所以配置在运行时由
    #  wrapper 自己写到 %APPDATA%\Everything\Everything.ini)
    makeWrapper $out/Everything.exe $out/bin/everything \
      --prefix PATH : ${pkgs.lib.makeBinPath [ pkgs.coreutils ]}

    cat > $out/bin/everything-bootstrap <<'EOF'
    #!/bin/sh
    # 真机部署:把 INI 模板铺到 $APPDATA/Everything/Everything.ini
    # (Linux 上跑这脚本会无声失败 — 这是 Windows-only 部署)
    set -e
    INI_DST="$APPDATA/Everything/Everything.ini"
    INI_SRC="@out@/share/Everything.ini.template"
    if [ "$(uname -o)" = "Msys" ] || [ -n "$WINDIR" ]; then
        mkdir -p "$(dirname "$INI_DST")"
        cp -f "$INI_SRC" "$INI_DST"
        echo "wrote $INI_DST"
    else
        echo "non-Windows host: skipping INI write" >&2
    fi
    EOF
    chmod +x $out/bin/everything-bootstrap
    substituteInPlace $out/bin/everything-bootstrap \
      --replace-fail '@out@' "$out"

    # Wrapper 启动入口:同 Everything.exe -startup 等价
    cat > $out/bin/everything-startup <<'EOF'
    #!/bin/sh
    exec @out@/Everything.exe -startup "$@"
    EOF
    chmod +x $out/bin/everything-startup
    substituteInPlace $out/bin/everything-startup \
      --replace-fail '@out@' "$out"
  '';

  # ============================================================================
  # OIagent Host Service 补丁 — 在已有 v0.19 systemd unit 上加 After/Requires
  # 让 Aureon OIagent 启动后能看到 Everything port 8765
  # ============================================================================
  aureon-oiagent-service-with-everything = pkgs.writeTextFile {
    name = "aureon-oiagent.service";
    text = ''
      [Unit]
      Description=Aureon v0.20.1 OIagent Host Service (with Everything tool)
      Documentation=https://github.com/aureon/aureon
      After=network.target aureon-everything.service
      Requires=aureon-everything.service

      [Service]
      Type=simple
      User=aureon
      Group=aureon
      WorkingDirectory=/var/lib/aureon
      ExecStart=${oiagent}/bin/aureon-oiagent --host 127.0.0.1 --port 18791
      Restart=on-failure
      RestartSec=5
      Environment="AUREON_HOME=/var/lib/aureon"
      Environment="LLM_PROXY=http://127.0.0.1:15721"
      Environment="EVERYTHING_HTTP_URL=http://127.0.0.1:8765/"

      [Install]
      WantedBy=multi-user.target
    '';

    # 把 oiagent 表达式从 auron.nix 同目录取
    oiagent = pkgs.callPackage ./oiagent.nix { };
  };
in
{
  # ============================================================================
  # 声明式配置 — 给 NixOS 配置.nix 直接 import
  # ============================================================================
  services.aureon.everything = {
    enable = true;
    package = everything-bin;
    http-port = 8765;
    http-bind = "127.0.0.1";
    autostart = "startup";       # 等价于 Everything.exe -startup
    exclude-paths = [
      "$Recycle.Bin"
      "System Volume Information"
      "C:\\Windows\\WinSxS"
    ];
    include-paths = [
      "C:\\"
      "D:\\"
      "E:\\"
    ];
    data-dir = "%APPDATA%\\Everything";
    db-compress = true;
    run-as-admin = true;
  };

  # ============================================================================
  # OIagent host service 的 systemd unit 升级版 — 自动带上 Everything dep
  # ============================================================================
  services.aureon.oiagent.service-file =
    aureon-oiagent-service-with-everything;

  # ============================================================================
  # 暴露给 LLM 的 tool schema — 跟 OIagent BUILTIN_TOOLS 形状一致
  # 这是声明式描述,真机由 aureon-oiagent.py 加载
  # ============================================================================
  services.aureon.oiagent.tools.builtin = [
    {
      name = "everything_status";
      kind = "python";
      description = "Everything HTTP server 健康快照";
      parameters = {
        type = "object";
        properties = {};
      };
    }
    {
      name = "everything_query";
      kind = "python";
      description = "Everything 全盘 NTFS 索引搜索";
      parameters = {
        type = "object";
        properties = {
          search = { type = "string"; description = "搜索 pattern"; };
          limit  = { type = "integer"; description = "结果上限 1-1024"; };
          path   = { type = "string"; description = "可选路径过滤"; };
        };
        required = [ "search" ];
      };
    }
  ];

  # ============================================================================
  # 元数据
  # ============================================================================
  meta = with pkgs.lib; {
    description = "Aureon v0.20.1 - Everything declarative NTFS index service";
    license = licenses.mit;
    platforms = [ "x86_64-windows" "x86_64-linux" ];
    maintainers = [ "aureon-team" ];
    broken = true;   # SHA256 占位,真用必填
  };
}
