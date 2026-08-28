# Aureon v0.19 Android emulator wrapper
# 走"工具不重复":Android SDK 已装在 Windows,Cygwin-style mount 走

{ pkgs ? import <nixpkgs> {} }:

pkgs.stdenv.mkDerivation {
  pname = "aureon-android-emulator";
  version = "34.0.0";

  src = pkgs.fetchurl {
    url = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip";
    sha256 = pkgs.lib.fakeSha256;
  };

  buildInputs = [ pkgs.unzip ];

  unpackPhase = "unzip $src";
  installPhase = ''
    mkdir -p $out
    cp -r cmdline-tools/* $out/
  '';

  meta = with pkgs.lib; {
    description = "Android emulator command-line tools (cmdline-tools)";
    license = licenses.asl20;
    platforms = [ "x86_64-linux" ];
  };
}