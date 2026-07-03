# Agent Shell 已知问题(联调阶段统一修复)

## 浮动球 — Windows 点击/拖动无响应 (defer)

**现象**

- 左键单击无反应,无法拖动
- 右键第一次触发「打断」类状态变化,第二次 Windows 报「未响应」

**根因(待修)**

1. `-transparentcolor` 透明窗口在 Windows 上,透明区域不接收鼠标事件;若球体绘制区域过小或事件未绑到 canvas item,点击会穿透
2. 右键 `_interrupt()` 在 UI 线程同步调 HTTP + 可能阻塞 tk mainloop
3. `_on_double` 与 `_on_release` 可能连环触发

**计划修复(整体联调阶段)**

- 改用分层窗口或 `-alpha` 半透明,避免 click-through
- 所有 orb 交互回调仅 `root.after(0, ...)` 投递,HTTP 放后台线程
- 拖动用 `<Button-1>` + `<B1-Motion>` 绑定到 canvas 所有 item(`tag_bind("all", ...)`)
- 右键改为队列化 interrupt,防重入

**临时替代**

- 使用托盘菜单切换 profile / 退出
- 热键: PTT / Ctrl+Shift+P / Esc 仍可用
- 可设 `ui.floating_orb: false`, `ui.status_bar: true` 回退顶栏
