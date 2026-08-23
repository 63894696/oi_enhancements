PrisirAI 开发者模式说明
=======================

本目录只在「开发者模式」装包时被安装到 $INSTDIR。
普通用户无需勾选。

=======================
安装内容
=======================
- git-portable/      完整 Git 命令行运行时(无 GUI,~55 MB)
- repo.zip            仓库源码打包(git archive HEAD,~2 MB,排除 dist/node_modules/.venv)

=======================
使用方式
=======================

1. 让 git 命令行生效
   - 把 $INSTDIR\git-portable\ 目录加进系统 PATH(用户级即可)
   - 或者用「开始菜单 → Prisir AI 开发者终端」快捷方式(已配好 PATH)
   - 命令行验证: git --version
                 → git version 2.51.0.windows.1

2. 拿到源码
   解压到任意工作目录:
     mkdir src && cd src
     unzip ../repo.zip
     cd oi_enhancements
     git status  # 应显示 "Not a git repository"
     git init .  # 如果要开始改并提交
     # 或:解压后我们后续推 zip 不带 .git,直接编辑即可

3. 修改后重打装包器(开发者常用)
   - PyInstaller 重打: pyinstaller PrisirAI-core.spec
     产物: ../dist/PrisirAI.exe(资源图标:火苗)
   - NSIS 重打: cd installer && makensis prisirai.nsi
     产物: ../dist/PrisirAI-Setup-${APP_VERSION}.exe

4. 用 oiagent 对话壳调试
   - 双击桌面「Prisir AI」图标 → 起 electron 壳
   - 装包后 oiagent-shell/ 在 $INSTDIR\oiagent-shell/,PrisirAI.exe 在 $INSTDIR\
   - 改 main.js / preload.js / oiagent_web.py 后需重打装包器才能生效

=======================
不包含什么
=======================
- python_embed/(用户自己装,见 oiagent-shell/README.md)
- node_modules/(用户自己装)
- PrisirAI.exe 的 site-packages(已冻结在 PyInstaller 二进制内)

=======================
更新方式
=======================
下次升级装包器时:
1. cd <开发机 repo> && git pull
2. 重新跑: git archive HEAD --format=zip --output=installer/_dev_assets/repo.zip
3. (git-portable 不需要更新,版本已经固化)
4. cd installer && makensis prisirai.nsi

=======================
回退
=======================
卸载 PrisirAI 时,本目录会随之删除(开发者模式默认总删)。
若要保留 git-portable,请在卸载前手动复制到别处。