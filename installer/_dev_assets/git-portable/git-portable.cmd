@echo off
:: git-portable.cmd — shim wrapper,把 git-portable 内部目录加进 PATH 再 exec git。
::
:: 用法:把 $INSTDIR\dev\git-portable\ 加进 PATH(目录级,不要直接用 .cmd),
::       任何命令行窗口都能直接 `git ...`,无需关心 mingw64/bin / usr/bin 细节。
::
:: 工作原理:
::   1. 解析脚本自身位置(%~dp0) → git-portable 根目录
::   2. 把根下 bin/ cmd/ mingw64/bin/ usr/bin/ 前置到本进程 PATH
::   3. exec git %*(绕过 cmd 自身的 quoting/redirect 限制)
::
:: 红线:
::   - 不修改注册表 / 系统 PATH,只在本进程有效。
::   - 不写任何文件到 %APPDATA%,完全无副作用。

setlocal
set "GP=%~dp0"
set "PATH=%GP%bin;%GP%cmd;%GP%mingw64\bin;%GP%usr\bin;%PATH%"
git %*
exit /b %ERRORLEVEL%