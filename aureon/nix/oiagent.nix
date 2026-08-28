# OIagent v0.19 package derivation — 不重写,描述 v0.18 已 ship
#
# 走"工具不重复":OIagent v0.18 已在 MuMu + AVD 上真跑 heartbeat 8000+
# 这里只描述依赖 + buildInputs,不重写代码

{ pkgs ? import <nixpkgs> {} }:

pkgs.stdenv.mkDerivation {
  pname = "aureon-oiagent";
  version = "0.19.0";

  # v0.18 已 ship 的 Python 脚本集(不重写)
  src = pkgs.fetchFromGitHub {
    owner = "aureon";
    repo = "aureon";
    rev = "v0.18.0";
    sha256 = pkgs.lib.fakeSha256;
  };

  # 真依赖:Python 3.12 + adb + curl
  buildInputs = with pkgs; [
    python312
    python312Packages.flask
    python312Packages.requests
  ];

  nativeBuildInputs = [ pkgs.makeWrapper ];

  # 真 install phase — wrap 脚本
  installPhase = ''
    mkdir -p $out/bin $out/lib
    cp -r $src/v018_initrc/* $out/lib/
    cp -r $src/oi_enhancements $out/lib/ 2>/dev/null || true

    makeWrapper ${pkgs.python312}/bin/python3 $out/bin/aureon-oiagent \
      --prefix PATH : ${pkgs.makeBinPath [ pkgs.curl pkgs.coreutils ]} \
      --set AUREON_HOME /var/lib/aureon \
      --add-flags $out/lib/aureon-oiagent.py

    makeWrapper ${pkgs.python312}/bin/python3 $out/bin/aureon-service \
      --set AUREON_HOME /var/lib/aureon \
      --add-flags $out/lib/aureon-service.py
  '';

  meta = with pkgs.lib; {
    description = "Aureon v0.19 OIagent host service wrapper";
    license = licenses.mit;
    platforms = [ "x86_64-linux" ];
  };
}