# 群聊可用性打磨任务包(SecureDM 形态3)

> 来源:2026-08-11 用户单账户实测 chatroom.html 发现的可用性问题。
> 决策已全部锁定,开发团队按本包实施,无需再问需求。
> 约束(不可违):
> - relay 保持纯管道:不验签(除 invite 门禁)、不解密、不存房间钥。
> - fragment `#k=` 永不上行服务器。
> - 房间钥/invite/token 不打印真值、不入日志(测试只断「相等/不等」布尔)。
> - E2E 参数与 `securedm_groupchat_e2e.py` / `chatroom.html` 现有协议逐字节对齐,不得擅自改 wire 格式。

## 涉及文件
- `chatroom.html`(Web 前端,主战场)
- `chatroom_relay.py`(WS 中继,仅 T2/T4 需动)
- `chatroom_bot.py`(bot 进程,T6)
- 新增:各改动对应测试脚本(沿用 `_test_groupchat_*.py` 风格)

---

## T1 主题统一(国画主题)

**问题**:chatroom.html 是独立暗色主题(`--bg:#0f1115`),与 SecureDM 主界面的国画主题(`assets/guohua-theme.css`)脱节。

**实施**:
- chatroom.html 引入 `assets/guohua-theme.css`,保留功能布局,只换视觉(背景/配色/字体/面板质感对齐主界面)。
- 国画背景图(`guohua_bg_panel.png` / `guohua_bg_wide.png`)按需用作面板/抽屉底,注意暗色可读性(文字对比度 ≥ 4.5)。
- 不动任何 JS 逻辑,纯样式层。

**验收**:打开 chatroom.html,视觉风格与 SecureDM 主界面同源(同一套国画主题);消息气泡、成员抽屉、房间抽屉在国画背景下文字清晰可读。

---

## T2 离开房间按钮(接通已有 relay leave)

**问题**:relay 已支持 `leave`(chatroom_relay.py:324,移除成员+广播 member-left+触发房主轮换),但前端无入口,用户只能关标签页(=离线,≠离开),导致前向保密的轮换永不触发。

**实施**:
- chatroom.html 加「离开房间」按钮(位置:header 或房间抽屉内)。
- 点击 → 二次确认(「离开后将无法解密本房新消息,确定?」)→ 发 `{"type":"leave","room","user_id"}` → 清空本地该房 RoomKeys + 从房间列表移除 → 回 setup 屏。
- 与「切换房间」(switching,仅断开不解散)区分:离开 = 显式放弃成员身份。

**验收**:A、B 两账户同房;A 点离开 → B 端收到 member-left;A 重进(若无 invite)能作为新成员加入;若 A 是房主且启用 E2E,B 收到轮换后的新 epoch 钥,A 持旧钥解不开新消息(前向保密)。

---

## T3 链接可点 + 新标签打开 + 危险检查面板(方案 B,核心价值)

**问题**:消息中 URL 被 `esc()` 全文转义成纯文本,无法点击;用户需要发会议资料链接,但点击不能跳离当前讨论页。且工具价值基础 = 最大程度帮用户过滤/发现危险。

**实施**(顺序严格,保 XSS 安全):
1. 消息渲染:先 `esc()` 转义整段文本(现状不变),再对转义后的纯文本做 URL 链接化(正则匹配 `https?://...`,包成 `<a>`)。**顺序不可反**。
2. `<a>` 强制 `target="_blank" rel="noopener noreferrer"`(新标签打开 + 防 window.opener 反控 + 不带 referer)。当前聊天页不跳转。
3. 点击链接**不直接打开**,先弹「链接安全检查」面板:
   - 完整 URL 明文展示(防短链/伪装)。
   - 自动检测:
     - 显示文本域名 vs 实际 href 域名是否一致(钓鱼经典)。
     - 同形字符检测(西里尔/希腊字母伪装,如 раypal)。
     - 短链展开预览(可选,离线则标注「短链,目标未知」)。
     - 本地恶意域名黑名单比对(复用 adblock-rust 能力或独立清单)。
     - 是否 HTTPS。
   - 风险分级着色:🟢可信 / 🟡未知 / 🔴可疑。
   - 操作按钮:[打开] [复制链接] [取消] [不再提示此域名(本地记白名单)]。

**验收**:
- 发含 `https://example.com/page` 的消息 → 对方看到可点链接 → 点击弹检查面板(不直接开)→ 面板显示完整 URL + 风险分级 → 点「打开」在新标签页打开,聊天页保持。
- 发伪装链接(显示文本写 `https://a.com` 实际 `https://evil.com`)→ 面板标红警示域名不符。
- 发 `<script>alert(1)</script>` 或含 HTML 的消息 → 纯文本显示,无脚本执行(XSS 回归)。

---

## T4 空闲房间过期回收

**问题**:`_ROOMS` 无任何清理逻辑(chatroom_relay.py),房间一旦创建永久存在(内存+CHATROOM_STATE 落盘),任何人可无限建房 → VPS 内存膨胀隐患。

**实施**:
- Room 加 `last_active`(每次 join/post/leave 更新)。
- relay 起后台定时任务(如每小时)扫描:空闲(无在线成员)且 `now - last_active > TTL`(默认 30 天,env `CHATROOM_ROOM_TTL_DAYS` 可调)的房间 → 从 `_ROOMS` 删除 + 落盘。
- 有在线成员的房间永不回收。
- 回收动作记日志(只记 room_id,不记内容)。

**验收**:起 relay,建房发消息,把 TTL 调到极小(如 1 分钟)测试 → 房间空置超时后被回收,重进同名房间是全新(owner 重置);有在线成员时不回收;CHATROOM_STATE 落盘同步移除。

---

## T5 新人看历史:维持方案 A(纯前向保密,不改)

**决策**:新人入房只拿当前 epoch 钥,历史消息对新人永久加密(密文可见但解不开)。上下文靠会前资料对齐(T3 链接分发),不靠翻聊天记录。**本项无代码改动**,仅确认现有行为正确并加测试固化。

**验收(测试固化)**:A 发若干 E2E 消息 → B 后入房收当前 epoch 钥 → B 能解入房后的新消息,解不开入房前的历史(解密失败符合预期)。

---

## T6 群机器人进程(@bot 有回应)

**问题**:@bot 发送逻辑正常(mentions 填 bot user_id),但无 bot 进程挂在房间,relay 纯管道不生成回复 → @bot 石沉大海。

**实施**:
- `chatroom_bot.py` 作为独立进程,以 `is_bot:true` join 指定房间,监听 @ 自己的消息(mentions 含自己 user_id)。
- 收到 @ → 调用本地 LLM 后端(接 Prisir AI 路由已有的 fastlane providers,复用现有配置)生成回复 → 以 `body.kind='bot'` 发回房间。
- E2E 房间:bot 需持有房间钥才能读/回(房主分发给 bot,或 bot 房为明文房)。**优先实现明文房 bot**,E2E 房 bot 的钥分发作为后续项。
- bot 不存储对话内容,回复仅基于当前被 @ 消息(无历史记忆,保隐私)。

**验收**:起 relay + bot 进程 + 两个客户端同房;A 发 `@bot 你好` → 数秒内收到 bot 回复(带 BOT 徽标);B 也能看到该回复;bot 离线时 @bot 仅发送不回(不报错)。

---

## 优先级与依赖

- P0(立即可做,无依赖):T1 主题、T2 离开按钮、T3 链接检查
- P1:T4 房间过期(动 relay)、T6 bot(依赖 fastlane 配置)
- P2(测试固化):T5
- 依赖:T3 的黑名单复用 adblock-rust 需确认其在 Web 端的可用形态(若不可用则先用独立清单);T6 依赖 fastlane providers 已配好。

## 整体验收

1. 全部改动后,跑通现有 `_test_groupchat_*.py`(e2e/invite/rotation/crossstack)不回归。
2. 新增 T2/T3/T4/T6 各自测试脚本全绿。
3. 浏览器实测:单账户能进房、能发链接点击弹检查面板、能离开房间、@bot 有回应(bot 进程开着时)。
