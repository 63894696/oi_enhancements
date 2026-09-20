# PrisirAI 扩展 UI 形态 B — 详细界面草图

> **2026-09-20 用户拍板:轻菜单型(顶栏 +1 按钮 + 右下角抽屉,普通用户友好)。**
> 三种状态(关闭/列表/详情) + 移动响应 + 跟现有顶栏风格对齐。

---

## 状态 0: 默认态(抽屉关闭,顶栏只有 🧩)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ 🔥 PrisirAI  │ 模型路由... │ 📂 Pri │ 📁 文件 │ 📑 文档 │ 🔑 模型 Key │          │
│ 📞 语伴 │ 🎵 音乐 │ 📅 日历 │ 🧩 扩展 │        [+ 新会话]                          │
├────────┬─────────────────────────────────────────────────────────┬──────────────┤
│ 会话    │ 对话主区                                                │ 资料/文件    │
│ ────── │                                                         │ ──────────── │
│ 新会话  │                                                         │              │
│ 会话 2 │ 用户:把刚才那个 mermaid 图渲染出来,字体用 JetBrains Mono  │              │
│ 会话 3 │                                                         │              │
│ ────── │ AI:好的,我用「设计稿渲染」扩展 ↓                      │              │
│ 📚 全部 │ ┌────────────────────────────────────────┐             │              │
│ 🗂 未分类│ │ ┌──────────────┐                       │             │              │
│         │ │ │  SVG 渲染图   │← 扩展自动注入的卡片  │             │              │
│         │ │ │  (扩展贡献)   │                       │             │              │
│         │ │ └──────────────┘                       │             │              │
│         │ │  💡 此卡片由「设计稿渲染 v0.3」扩展提供 │             │              │
│         │ └────────────────────────────────────────┘             │              │
│         │                                                         │              │
│         │ 问点什么...                                       [发送] │              │
└────────┴─────────────────────────────────────────────────────────┴──────────────┘
                                         ↑ 顶栏新增 1 个按钮(红框位置)
```

**说明**:
- 普通用户看 AI 对话就跟今天完全一样,**完全感受不到「扩展」存在**
- 「设计稿渲染」扩展做的卡片 AI 自然生成,用户感知是「AI 变厉害了」
- 只有开发者/高级用户才会主动点 `🧩 扩展` 按钮

---

## 状态 1: 抽屉展开(主列表态)

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│ 🔥 PrisirAI  │ ... │ 📂 Pri │ 📁 │ 📑 │ 🔑 │ 📞 │ 🎵 │ 📅 │🧩│ [+ 新会话]        │ ← 🧩 高亮(active)
├────────┬─────────────────────────────────────────────────────────┬──────────────┤
│ 会话    │ 对话主区                                                │ 资料/文件    │
│ ...    │ ...                                                     │ ...          │
│         │                                                         │              │
│         │                                                         │              │
│         │                                                         │              │
│         │                                                         │  ┌──────────┐│
│         │                                                         │  │🧩 扩展   ││ ← 右下角抽屉
│         │                                                         │  │──────────││
│         │                                                         │  │✅ 设计稿渲││
│         │                                                         │  │   v0.3   ││
│         │                                                         │  │ ⏸ SQL查询 ││
│         │                                                         │  │   v1.2   ││
│         │                                                         │  │ ❌ 时间线 ││
│         │                                                         │  │   v0.1   ││
│         │                                                         │  │   [启用] ││
│         │                                                         │  │──────────││
│         │                                                         │  │ 🛒 浏览更 ││
│         │                                                         │  │   多 →   ││
│         │                                                         │  │──────────││
│         │                                                         │  │ 🔄 检查更 ││
│         │                                                         │  │   新      ││
│         │                                                         │  └──────────┘│
└────────┴─────────────────────────────────────────────────────────┴──────────────┘
```

**抽屉规格**:
- 位置:`position:fixed; right:20px; bottom:20px`
- 宽:340px,高:`min(420px, 60vh)`,**最多 60% 视口**,不挤压主对话
- 背景:`var(--gh-paper)`,圆角 14px,阴影 `0 8px 24px rgba(0,0,0,.15)`,跟现有 modal 同风格
- 入场:从右下角 scale(0.92) + opacity 0 → 1,180ms ease-out
- 点击外部 / Esc / 再次点 🧩 → 关闭

**每行 item**:
```
┌────────────────────────────────────┐
│ ✅ 设计稿渲染           v0.3  ⚙  │ ← ✅ 已启用 / ❌ 已禁用 + 版本号 + 齿轮(详情)
│   在 AI 对话里渲染 SVG/图表卡片   │ ← 一句话说明
└────────────────────────────────────┘
```

---

## 状态 2: 抽屉展开 + 进入某个扩展详情

```
                                                       ┌──────────────────────┐
                                                       │🧩 扩展 / 设计稿渲染 ←│ ← 面包屑可点回
                                                       │──────────────────────│
                                                       │  ✓ 已启用    v0.3    │
                                                       │──────────────────────│
                                                       │ 作者: Prisir 官方    │
                                                       │ 更新: 2026-09-15     │
                                                       │ 大小: 12KB           │
                                                       │ 权限: AI 调用渲染    │
                                                       │                      │
                                                       │ 📝 描述              │
                                                       │ ──────────────────── │
                                                       │ 在 AI 对话中自动识别 │
                                                       │ mermaid/graphviz/svg │
                                                       │ 代码块,渲染为高保真  │
                                                       │ 卡片。支持导出 PNG。 │
                                                       │                      │
                                                       │ ⚙ 设置              │
                                                       │ ──────────────────── │
                                                       │ 渲染引擎  [Mermaid ▾]│
                                                       │ 主题      [Auto    ▾]│
                                                       │ 导出格式  [PNG/SVG]  │
                                                       │                      │
                                                       │ 🔧 操作              │
                                                       │ ──────────────────── │
                                                       │ [⏸ 禁用] [🗑 卸载]   │
                                                       │ [🔄 检查更新]         │
                                                       └──────────────────────┘
```

**权限透明化**:每个扩展都列「AI 调用了什么 / 访问了什么文件 / 是否联网」,用户能一眼看明白。
- 跟 PrisirAI 现有的 [权限闸弹卡 UI v1](../memory/prisIrAI-perm-card-ui-v1.md) 复用同一套风险级配色

---

## 状态 3: 「浏览更多」 → 商店(简化版)

```
                                                       ┌──────────────────────┐
                                                       │🧩 扩展 / 浏览更多  ✕│
                                                       │──────────────────────│
                                                       │ 🔍 [搜索扩展...     ]│
                                                       │ 分类: [全部 ▾] 排序: │
                                                       │         [热门 ▾]    │
                                                       │──────────────────────│
                                                       │ 📦 设计稿渲染 v0.3   │
                                                       │   Prisir 官方 · 12K  │
                                                       │   ⭐⭐⭐⭐☆  2.1K 安装 │
                                                       │   [安装]              │
                                                       │──────────────────────│
                                                       │ 📦 文档大纲 v1.0     │
                                                       │   社区 · 8K          │
                                                       │   ⭐⭐⭐⭐⭐  870 安装  │
                                                       │   [安装]              │
                                                       │──────────────────────│
                                                       │ 📦 时间线视图 v0.1   │
                                                       │   实验性 · 4K        │
                                                       │   ⭐⭐☆☆☆  120 安装   │
                                                       │   [安装]              │
                                                       │──────────────────────│
                                                       │      < 1 / 23 >      │
                                                       └──────────────────────┘
```

**商店源**:
- v0.1 只读 GitHub 仓库 `PrisirAI/extensions` 的 `index.json`(策划过的列表)
- 不开放任意 URL 安装(防供应链)
- 后续版本再考虑本地 .zip 安装 + 签名校验

---

## 状态 4: 安装中(进度)

```
                                                       ┌──────────────────────┐
                                                       │🧩 扩展 / 设计稿渲染  │
                                                       │──────────────────────│
                                                       │   ⏳ 下载中... 67%   │
                                                       │   ████████░░░░░░░░░░ │
                                                       │   8KB / 12KB         │
                                                       │                      │
                                                       │   校验 SHA256... ✓   │
                                                       │   解压到 ext/mermaid/│
                                                       └──────────────────────┘
```

---

## 状态 5: 安装完触发权限闸(首次启用)

```
                ┌────────────────────────────────────────────┐
                │ 🛡 权限请求 — 设计稿渲染 v0.3                │
                │ ────────────────────────────────────────── │
                │ 此扩展申请以下权限:                         │
                │                                             │
                │  ⚠ AI 调用 — 扩展可被 AI 在对话中调用       │
                │    (高风险 · 红色)                          │
                │  ℹ 文件渲染 — 在浏览器里渲染 SVG/PNG        │
                │    (低风险 · 黄色)                          │
                │                                             │
                │ 此扩展由「Prisir 官方」签名                  │
                │ 哈希: a3f5...c7e9  ✓ 已校验                 │
                │                                             │
                │ [❌ 拒绝]  [✓ 同意,仅本次]  [✓ 始终允许]    │
                │                                             │
                │          12 秒后自动拒绝                    │
                └────────────────────────────────────────────┘
```

**跟现有权限闸一致性**:复用 `prisIrAI-perm-card-ui-v1.md` 的配色与倒计时。

---

## 移动/窄视口适配(< 900px)

抽屉变全屏 bottom sheet:
```
┌──────────────────────────────────────┐
│  🧩 扩展                       ✕    │ ← 顶部条
├──────────────────────────────────────┤
│ ✅ 设计稿渲染           v0.3    ⚙   │
│ ⏸ 时间线              v0.1    ⚙    │
│ ❌ SQL查询             v1.2    ⚙    │
│ ─────────────────────────────────── │
│ 🛒 浏览更多 →                        │
│ 🔄 检查更新                          │
└──────────────────────────────────────┘
```
- 抽屉占据 `bottom:0; left:0; right:0; height:60vh`
- 圆角只在顶部,带下滑手势提示条

---

## 跟现有顶栏对齐的 CSS 草案

```css
/* 顶栏 🧩 按钮(active 态跟其他 modal 一致) */
.topbtn.active { background: var(--gh-paper-2); border-color: var(--gh-green-deep); }

/* 抽屉容器 */
#ext-drawer { position: fixed; right: 20px; bottom: 20px; width: 340px;
  max-height: min(420px, 60vh); background: var(--gh-paper);
  border-radius: 14px; box-shadow: 0 8px 24px rgba(0,0,0,.15);
  z-index: 110; /* 跟 projmodal 111 错开,跟 keymodal 105 同级 */
  display: none; flex-direction: column; overflow: hidden;
  transform-origin: bottom right;
  transition: opacity .18s ease-out, transform .18s ease-out;
}
#ext-drawer.open { display: flex; opacity: 1; transform: scale(1); }
#ext-drawer.closed { opacity: 0; transform: scale(.92); }

/* 抽屉头部 */
#ext-drawer .head { padding: 14px 16px; border-bottom: 1px solid var(--gh-line);
  display: flex; align-items: center; gap: 8px; font-weight: 600;
  color: var(--gh-green-deep); }
#ext-drawer .head .crumb { font-size: 12px; color: var(--gh-ink-faint);
  margin-left: auto; cursor: pointer; }

/* item 行 */
#ext-drawer .item { padding: 10px 16px; display: flex; flex-direction: column;
  gap: 2px; border-bottom: 1px dashed var(--gh-line); cursor: pointer;
  transition: background .12s; }
#ext-drawer .item:hover { background: var(--gh-paper-2); }
#ext-drawer .item .title { font-size: 13px; font-weight: 600;
  color: var(--gh-ink); display: flex; align-items: center; gap: 6px; }
#ext-drawer .item .desc { font-size: 11px; color: var(--gh-ink-faint); }
#ext-drawer .item .ver { font-size: 10px; color: var(--gh-ink-faint);
  margin-left: auto; padding: 1px 5px; border-radius: 3px;
  background: var(--gh-paper-2); }

/* 底部固定操作区 */
#ext-drawer .foot { padding: 12px 16px; border-top: 1px solid var(--gh-line);
  display: flex; flex-direction: column; gap: 6px;
  font-size: 12px; }
#ext-drawer .foot a { color: var(--gh-green-deep); cursor: pointer; }

/* 详情视图 */
#ext-detail { display: none; padding: 16px; overflow-y: auto; flex: 1; }
#ext-detail.show { display: block; }
#ext-detail .section { margin-bottom: 14px; }
#ext-detail .section h4 { font-size: 11px; text-transform: uppercase;
  color: var(--gh-ink-faint); letter-spacing: .5px; margin-bottom: 6px; }
```

---

## 数据流(抽屉打开时)

```
用户点 🧩
  ↓
fetch('/prisiragent/api/extensions?op=list')
  ↓ 后端读 ~/.prisir/extensions/installed.json
  ↓ 返 [{ id, name, version, enabled, desc }]
  ↓
渲染抽屉主列表
  ↓
用户点某扩展
  ↓
渲染详情(直接展开抽屉内 #ext-detail)
  ↓ 不重 fetch(数据已在内存)
  ↓
用户操作(禁用/卸载/改设置)
  ↓
POST /prisiragent/api/extensions?op=disable|uninstall|config
  ↓ 后端更新 installed.json + 重启该扩展进程
  ↓ 抽屉内状态原地刷新
```

---

## i18n keys 新增

```javascript
// zh
ext_panel: '扩展', ext_installed: '已装扩展', ext_store: '浏览更多',
ext_check_update: '检查更新', ext_disable: '禁用', ext_enable: '启用',
ext_uninstall: '卸载', ext_perm_title: '权限请求',
ext_settings: '设置', ext_author: '作者', ext_updated: '更新',
ext_size: '大小', ext_perm: '权限', ext_desc: '描述',
ext_empty: '还没有安装任何扩展', ext_installing: '安装中...',
ext_verifying: '校验 SHA256...', ext_verified: '✓ 已校验',
ext_unpacked_to: '解压到',
// en
ext_panel: 'Extensions', ext_installed: 'Installed', ext_store: 'Browse more',
ext_check_update: 'Check updates', ext_disable: 'Disable', ext_enable: 'Enable',
ext_uninstall: 'Uninstall', ext_perm_title: 'Permission request',
ext_settings: 'Settings', ext_author: 'Author', ext_updated: 'Updated',
ext_size: 'Size', ext_perm: 'Permissions', ext_desc: 'Description',
ext_empty: 'No extensions installed yet', ext_installing: 'Installing...',
ext_verifying: 'Verifying SHA256...', ext_verified: '✓ Verified',
ext_unpacked_to: 'Unpacked to',
```

---

## 三种形态对比回顾(用户已选 B)

| 维度 | A 纯对话型 | **B 轻菜单型(已选)** | C IDE 型 |
|------|-----------|---------------------|----------|
| 普通用户友好度 | ★★★★★ | ★★★★☆ | ★★☆☆☆ |
| 高级用户控制力 | ★★☆☆☆ | ★★★★☆ | ★★★★★ |
| 顶栏 UI 增量 | +1 按钮 | +1 按钮 | +3 tab |
| 实现工作量 | 中(需工具注册层) | 中+抽屉 | 大 |
| 跟现有顶栏冲突 | 无 | 无 | 较冲突 |
| 扩展作者能见度 | 低 | 中 | 高 |
| 适合路线 | Node first | **Node first + 渐进** | Tauri/VSCode 改造 |

**B 形态优势**:抽屉式不进主对话流,展开/收起成本极低,既给普通用户「无感升级」也给高级用户「随时接管」。

---

## 下一步

1. 等用户在 [[prisIr-extension-roadmap-2026-09-20]] 拍板 Node vs Rust+Tauri 主路线
2. 拍板后,在 Web 端(Python 内嵌)先出 v0.1 后端:`/api/extensions` + `installed.json`
3. 前端:加 🧩 按钮 + 抽屉 HTML/CSS,按本文件草图落地
4. 做一个示例扩展(让设计稿渲染实际能跑通),证明端到端

**注意**:全程不打安装包,等所有功能 ship 后一并打。