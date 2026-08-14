# GitHub 连接器方案 v2 — openwork 主导(2026-08-14 重写,待拍板)

> v1(`github-connector-plan-2026-08-13.md`)按「浏览器插件 REST」写。用户战略修正(2026-08-14):**我们和 Comet 的本质差异是有 openwork(本地系统智能体)**。GitHub 这类「开发项目」的代码和凭证都在本地,本就该 openwork 主导;浏览器只补它够不着的一环。本版按 openwork 主导重画。
> 本文定分工与链路,**先文档后动工,红线先拍板再动手**。
> 串行链位置:P3(连接器写操作)/ CW-3 的一部分。这套「三层分工」一旦定稿,P1 securedm、P3 其它站点写操作都套同一套。

---

## 0. 一句话定位

GitHub 连接器是 **openwork 的一个技能**,不是浏览器插件功能。**机械步骤(openwork)做满,权责边界(花钱/授权打勾)交人。** 浏览器侧只做「读」和「弹确认」。

**核心分工哲学:机器代劳劳力,人守住权责。** 与翻译插件 L1 确认卡同一套。

---

## 1. 三层分工(定稿)

| 层 | 角色 | 在 GitHub 连接器里干什么 |
|----|------|------------------------|
| **浏览器智能体** | 读 + 轻交互 + 弹确认 | read_page_tab 读公开页(trending/repo);NTP 弹确认卡/选择卡 |
| **openwork(本地系统智能体)** | 系统/本地/API/驱动浏览器自动化 | 直调 GitHub REST(建仓/issue/star);git 本地操作;驱动浏览器自动化到授权页 |
| **用户** | 终判(花钱 + 授权) | 付费确认、建 token 勾 scope 的最后一勾 |

### 1.1 按「谁的能力最贴合」划分(不按谁方便)

| 环节 | 谁做 | 为什么 |
|------|------|--------|
| 建仓 / 写 issue / star / fork | **openwork**(本地直调 REST API) | 纯 API 调用,本地系统智能体最干净——不碰浏览器、不碰登录态;凭证走本地 keyring,天然符合「项目都在本地」 |
| 管理本地 repo(clone/push/分支) | **openwork** | 本地系统层的事,浏览器完全够不着 |
| **建 PAT / 勾 scope / 付费授权页** | **openwork 驱动浏览器自动化,到关键确认停** | 「点菜单/填表/下拉选品名/打勾权限」openwork 做完前置,**在「创建 token」「确认付费」这类不可逆/花钱的一步停下,交回用户亲手点** |
| 读公开网页(trending 等) | 浏览器智能体(read_page_tab) | 已有能力,读取是浏览器本职 |

---

## 2. 「openwork 技能 + 浏览器自动化到授权页 + 付费/打勾交人」链路

以两个典型场景画链。

### 场景 A:建仓(纯 API,无付费/授权页)—— openwork 全程,人只在 L1 确认卡

```
用户在 NTP 或 openwork 对话:「在 github 建个叫 X 的仓,备注 Y,私有」
  │
  ▼
openwork 接意图 → 映射 gh_create_repo(name, description, private)
  │
  ▼
【确认卡】弹「将在你的 GitHub 建私有仓 X,描述 Y」→ 用户点确认   ← 唯一的「人」节点(L1)
  │
  ▼
openwork 本地直调 POST https://api.github.com/user/repos
  (PAT 走 Authorization header,从本地 keyring 取,不落 LLM/日志/审计明文)
  │
  ▼
返回仓地址 → openwork 可选 git init/clone 到本地
```

**这条链没有浏览器自动化**——建仓是纯 API,openwork 本地搞定,人只在确认卡点一次。

### 场景 B:建 token 选权限(涉及授权页)—— openwork 自动化到「打勾/创建」前停,交人

```
用户:「帮我建一个有 repo 权限的 token」
  │
  ▼
openwork 驱动浏览器(用我们自己的 Prisir 浏览器,自动化接口)
  → 打开 https://github.com/settings/tokens/new
  → 自动填 note(如 "prisir-openwork")、选 expiration
  → 自动展开 scope 区、勾上 repo(机械步骤做满)
  │
  ▼
【停在「Generate token」按钮前】   ← 关键授权节点,交人
  openwork 提示:「我已填好 note 和 repo 权限,请你核对后亲手点 Generate」
  │
  ▼
用户亲手点 Generate → 拿到 token → 粘回(或 openwork 读页面一次性抓取)
  │
  ▼
openwork 校验 token(GET /user 读出 scope 展示「这个 token 有 repo 权限」)
  → 加密存本地 keyring(不落 LLM/审计明文)
```

**这条链的「浏览器自动化」由 openwork 驱动**(不是浏览器插件),因为我们有本地系统智能体——Comet 没有这层,只能在浏览器沙箱里驱动 DOM。**停在「Generate/付费」前交人**,是权责边界。

### 场景 C:付费/订阅(若涉及)—— 同 B,停在「确认支付」前

任何花钱的一步(买 plan、开通付费功能),openwork 自动化把表单填好,**停在「确认支付」按钮前**,用户亲手扫码/点付。与①③的「token 中转站付费(加密货币/支付宝/微信扫码)」同一交人原则。

---

## 3. 凭证红线(贯穿,沿用并强化)

- [ ] PAT 存**本地 keyring / openwork 安全存储**(不是浏览器插件 storage),`ghp_`/`github_pat_` 前缀**永不进 LLM 上下文、不落审计明文、不落日志、md 不回显**。
- [ ] openwork 调 API 时 token 走 `Authorization: Bearer` header;审计只记「动作+非敏感参数」(如 `gh_create_repo 名=X`),不记 token。
- [ ] 每个 L1 写动作(建仓/issue/star/fork)都有**确认卡**;**不批量、不静默**。
- [ ] **建 token / 付费**这两类,openwork 自动化**必须停在最终确认前**,由用户亲手完成——这是硬红线,不可省。
- [ ] API 白名单只放 `api.github.com`;openwork 不带 token 访问任何非 GitHub 域。
- [ ] E2E 用 GitHub **测试账号/专用 test repo**,token 走环境变量,建仓即删,不碰真实主仓。

---

## 4. openwork 技能接口(落 openwork 侧)

在 openwork 注册 `github` 技能,动作白名单:

| action | risk | confirm | 说明 |
|--------|------|---------|------|
| `gh_list_repos` | L0 | none | 列我的仓库(只读) |
| `gh_read_issues` | L0 | none | 读 issue 列表 |
| `gh_create_repo` | L1 | **card** | 建仓(名/描述/私有),确认卡 |
| `gh_create_issue` | L1 | **card** | 开 issue,确认卡 |
| `gh_star` / `gh_fork` | L1 | **card** | star/fork,确认卡 |
| `gh_git_ops` | L1 | **card** | 本地 git clone/push/分支(openwork 系统层) |
| `gh_setup_token` | L2 | **浏览器自动化+人工打勾** | 场景 B:自动化到授权页,停在 Generate 前交人 |

**token 未配置**:任何 gh_* 先查 keyring,无则引导走 `gh_setup_token`(场景 B)或提示手动建 PAT。

---

## 5. 与浏览器智能体的边界(别混)

- **浏览器智能体(read_page_tab)**:只读公开页;不做 GitHub 写操作。
- **openwork**:GitHub 写操作 + 本地 git + 驱动浏览器自动化到授权页。
- **分工锚点**:**「读」在浏览器,「写/本地/API/自动化」在 openwork,「花钱/授权打勾」在人。**
- 用户在浏览器 NTP 发起 GitHub 写意图时,浏览器智能体**转交 openwork**(经 coworker 桥 127.0.0.1+X-OI-Token 白名单),不自己动手。

---

## 6. 实施步骤(拍板后)

1. **M1 openwork github 技能骨架**:注册动作白名单 + 凭证 keyring 存取 + `GET /user` 校验。E2E:配 token→验证→列出权限。
2. **M2 只读动作**:`gh_list_repos`/`gh_read_issues`。E2E:列测试账号仓库。
3. **M3 写动作+确认卡**:`gh_create_repo`(确认卡→建仓→返回 url)。E2E 测试账号建仓即删。
4. **M4 issue/star/fork + 本地 git**:`gh_create_issue`/`gh_star`/`gh_fork`/`gh_git_ops`。
5. **M5 建 token 自动化(场景 B)**:openwork 驱动 Prisir 浏览器到 tokens/new,自动填表,停在 Generate 前交人;粘回后校验+加密存。
6. **M6 浏览器智能体转交**:NTP 收到 GitHub 写意图 → 转 openwork(coworker 桥)。
7. 全程红线回归(凭证隔离/审计打码/确认卡/自动化停在交人点)。

---

## 7. 待拍板汇总

1. **openwork 主导地位**是否确认?(本版核心:GitHub 写操作归 openwork,不归浏览器插件)
2. **「自动化停在交人点」**(建 token 停在 Generate 前 / 付费停在确认支付前)作为硬红线,是否确认?
3. **浏览器自动化通道**:openwork 驱动 Prisir 浏览器,用哪套自动化接口(CDP / 我们自有的 openwork 浏览器桥)?M5 前需定。
4. **建仓默认私有还是公开?**(沿用 v1 建议:默认私有)
5. **排期**:GitHub 连接器(openwork 技能)与 P1 securedm、① NTP 源码改造,三者先后?
6. **E2E 测试账号/token**:提供测试 token 走环境变量(不贴字面值)。
