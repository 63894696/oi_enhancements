# SecureDM Shell(Tauri 桌面壳)— 已归档 / DEPRECATED

> **状态:已归档,不再开发(2026-08-19)。**

## 为什么归档

这是**浏览器内置加密通信开发之前**的过渡产物:一个独立的 Tauri 桌面壳,承载
SecureDM 匿名加密通信(WebView2 + 内嵌 `securedm_web.py` 页面)。

现架构方向已变:**匿名加密通信整合进 Prisir 浏览器本体**,不再以独立桌面壳形态存在。
因此本壳停止开发,从统一编译清单中剔除。

## 保留原因

文件保留作参考,不删除:
- `src-tauri/` 的 Tauri 配置(`tauri.conf.json` / `capabilities` / `permissions`)
  对未来「若浏览器某能力需独立壳承载」仍有参考价值。
- 群聊/私信的实际逻辑不在本壳,而在仓库根的 `chatroom_*.py` / `securedm_web.py` /
  `securedm_groupchat_e2e.py` / `crypto_conduit/`(均已提交保留)。

## 不要做的

- 不要把本目录纳入 cargo 统一编译(它不是 cdylib,是 Tauri 应用,且已废弃)。
- 新功能不要加在这里;加密通信的后续开发走浏览器集成路径。
