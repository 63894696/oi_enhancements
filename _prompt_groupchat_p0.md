你是 SecureDM 群聊前端开发 agent。先 Read 完整任务规范 `C:\Users\Administrator\oi_enhancements\_task_groupchat_polish.md`,只实施其中 **P0 三项纯前端任务: T1 / T2 / T3**,严格不改 relay(chatroom_relay.py)、不动任何生产服务。

工作目录:`C:\Users\Administrator\oi_enhancements`
主战场文件:`C:\Users\Administrator\oi_enhancements\chatroom.html`

严格遵守任务包顶部约束:
- fragment `#k=` 永不上行;房间钥/invite/token 不打印真值、不入日志。
- E2E 参数与现有协议逐字节对齐,不改 wire 格式。
- relay 保持纯管道。

本次只做这三项,逐项实现并自测:

## T1 国画主题统一
- chatroom.html 引入 `assets/guohua-theme.css`(先 Read 它了解变量/类),把现有独立暗色主题(--bg:#0f1115 等)替换为国画主题视觉。
- 保留全部功能布局与 JS 逻辑,只动样式。背景可用 `assets/guohua_bg_panel.png` / `guohua_bg_wide.png`,确保消息气泡/成员抽屉/房间抽屉文字对比度 ≥ 4.5 可读。
- 若 guohua-theme.css 的变量名与 chatroom.html 现有 :root 变量不同,做映射适配,不要破坏现有 class 结构。

## T2 离开房间按钮
- relay 已支持 `leave` 消息(见 chatroom_relay.py 协议注释),前端补入口。
- header 加「离开」按钮 → 二次确认(「离开后将无法解密本房新消息,确定?」)→ 发 `{"type":"leave","room":room,"user_id":ID.user_id}` → 清空本地该房 RoomKeys[room] + 从房间列表 localStorage 移除该房 → 回 setup 屏。
- 与「切换房间」(switching 标志,仅断开不解散)区分开:离开 = 显式放弃成员身份。

## T3 链接可点 + 新标签打开 + 危险检查面板
- 现状:esc() 把消息全文转义成纯文本,URL 无法点击。
- 实施(顺序严格,保 XSS 安全):先 esc() 转义整段(现状不变),再对转义后的纯文本做 URL 链接化(正则匹配 https?://...),包成 `<a target="_blank" rel="noopener noreferrer">`。顺序不可反。
- 点击链接不直接打开,先弹「链接安全检查」面板:
  - 完整 URL 明文展示。
  - 检测:显示文本域名 vs 实际 href 域名是否一致;同形字符(西里尔/希腊字母伪装);是否 HTTPS;本地恶意域名清单比对(若 adblock-rust 在 Web 端不可用,先用一个独立的小清单常量)。
  - 风险分级着色 🟢/🟡/🔴。
  - 按钮:[打开] [复制链接] [取消] [不再提示此域名(写 localStorage 白名单)]。
- 点「打开」在新标签页打开,聊天页不跳转。

## 完成后自测(用 run_shell / 读文件验证,不要假装测过)
- 在 chatroom.html 同目录生成 `_test_groupchat_polish.html` 或一个简短 Node/Python 静态检查脚本,验证:
  1. 含 `<script>alert(1)</script>` 的消息经你的链接化后仍是纯文本(XSS 回归)。
  2. 含 `https://example.com` 的文本被包成带 target=_blank + rel=noopener 的锚点。
  3. 显示文本域名 ≠ href 域名时,检查面板判定为可疑(🔴)。
- 如实报告:哪项真测了、哪项没法本地测(如国画主题视觉需人工看),不要写假 PASS。

全部完成且自测通过后,输出 DONE 并附:改动文件清单 + 每项自测结果 + 未能本地验证需人工确认的点。
