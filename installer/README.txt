Prisir(湃睿思) AI — 本地对话助手
================================

启动方式
--------
桌面双击 "Prisir AI" 快捷方式,或开始菜单 PrisirAI → Prisir AI。

工作目录
--------
默认: %USERPROFILE%\Documents\PrisirAI\
- 写文件/Shell 命令在此目录下默认放行
- 出此目录的写/危险命令需要用户确认卡

审计
----
日志: <安装目录>\logs\audit\permission_stream.jsonl
每次权限闸决策写一行 JSONL(risk_level / allow / reason / ts)。

更新
----
v1.1 起会有自动更新。v1.0 需手动重装。

卸载
----
控制面板 → 程序 → 卸载程序 → 选 "Prisir(湃睿思) AI"。

问题反馈(v2.0 起)
--------
点击窗口右上「⚙ 反馈问题」按钮 → 弹卡 → 「发布到反馈论坛」。
系统自动打 zip(脱敏 settings.json,不含会话正文)+ 打开论坛反馈页。
zip 在桌面,可手动附加到论坛帖。

开发者模式(可选)
--------
安装向导勾选「PrisirAI 开发者模式」会额外装入:
- <安装目录>\dev\git-portable\   Git 命令行运行时(~55 MB)
- <安装目录>\dev\repo.zip          仓库源码(~2 MB,排除 dist/node_modules/.venv)
- <安装目录>\dev\DEV_README.txt    重打装包器操作指引

装好后:
- 托盘右键菜单出现「开发者模式」分组
- 「打开开发者终端」立即可用 `git --version`
- 「查看开发者说明」打开 DEV_README.txt

详见 <安装目录>\dev\DEV_README.txt。

开发者模式仅供修改源码/调试装包器时使用。普通用户请勿勾选。
