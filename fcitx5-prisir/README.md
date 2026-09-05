# 灵犀拼音 Prisir Pinyin · fcitx5 addon (Linux)

## 内容
- prisir.so            fcitx5 输入法引擎 addon(拷到 /usr/lib/x86_64-linux-gnu/fcitx5/)
- libprisir_ime.so     拼音引擎(Rust cdylib,运行期 dlopen;拷到 /usr/lib/fcitx5/prisir/)
- ciku.db              词库(拷到 /usr/lib/fcitx5/prisir/)
- prisir-addon.conf    addon 描述(拷到 /usr/share/fcitx5/addon/prisir.conf)
- prisir-inputmethod.conf 输入法声明(拷到 /usr/share/fcitx5/inputmethod/prisir.conf)
- prisir.cpp / prisir_engine.h  addon 源码(复现用)

## 安装(推荐用 .deb)
  sudo dpkg -i fcitx5-prisir_0.1.0_amd64.deb
  # 然后在 fcitx5 配置加「灵犀拼音」,重启 fcitx5:fcitx5-remote -r 或注销重登

## 手动安装(等价于 .deb)
  sudo cp prisir.so /usr/lib/x86_64-linux-gnu/fcitx5/
  sudo cp prisir-addon.conf /usr/share/fcitx5/addon/prisir.conf
  sudo cp prisir-inputmethod.conf /usr/share/fcitx5/inputmethod/prisir.conf
  sudo mkdir -p /usr/lib/fcitx5/prisir
  sudo cp libprisir_ime.so ciku.db /usr/lib/fcitx5/prisir/
  # 重启 fcitx5

## 重新编译 addon
  依赖:sudo apt install fcitx5-modules-dev libfcitx5core-dev libfcitx5utils-dev cmake g++
  cmake -B build -S . && cmake --build build
  # 引擎 libprisir_ime.so 由 prisir_ime crate(cdylib)cargo build --release 产出

## 环境变量
  PRISIR_IME_HOME  覆盖引擎/词库目录(默认 /usr/lib/fcitx5/prisir)
