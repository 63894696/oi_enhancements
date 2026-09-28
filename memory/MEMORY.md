- [HackerNews Algolia 集成 PrisirAI](hn-prisir.md) — 2026-09-26 ship,免 key,hn_search provider 始终注册;56/56 绿(含真探活);Reddit 改换决定
- [Exa MCP 集成 PrisirAI](exa-prisir.md) — 2026-09-26 ship,env 触发 exa_search provider + 4 capability;L0 只读;52/52 绿
- [PrisirAI 间接接入审计](audit-direct-vs-indirect.md) — 2026-09-26 ship,4 处子进程桥(agent-reach/Easel 6/Easel 9/port_registry)全是设计意图,无需直接化;7 项真上游已直接整合
- [GitHub agent 能力层项目调研](agent-tool-survey.md) — 2026-09-26,8 大类 50+ 项目 4 档决策:Playwright MCP P2 推迟 / 其它多不接(重叠 Easel/付费/产品边界)
- [Playwright MCP 集成 PrisirAI](playwright-mcp-prisir.md) — 2026-09-26 ship,stdio JSON-RPC 子进程桥(7 工具),threading.Lock 非可重入坑修复,60/60 绿
- [agent-browser 集成 PrisirAI](agent-browser-prisir.md) — 2026-09-26 ship,vercel-labs/agent-browser Rust CLI 子进程桥,@eN refs 稳定 ~93% token 削减,click/fill=L1 余 L0;64/64 verify 绿
- [screenshot-mcp 集成 PrisirAI](screenshot-prisir.md) — 2026-09-26 ship,joeblack-lha/screenshot-mcp 桌面截图(Win PowerShell / macOS screencapture / Linux grim-scrot),补完浏览器外场景;6 capability 全 L0;35/35 + 68/68 verify 绿

## M3.66 classification-head 路径(活跃)
- [M3.66 立项 classification-head 替换路径 2026-09-23](prisIr-companion-m366-classification-head.md) — 8 scenario 用 AgentJev 风格 classification head 替换 15 LoRA;证据:parse_fail 0%/延迟 100x/Brier 5-10x;L1 调研 + L2 aliyun T4 训 demo + L3 8 scenario + L4 双通道 fallback

## M3.64 5 scenario critical 专项 + Obsidian 复活(活跃,本地完成)
- [M3.64 5 scenario critical 专项 + M3.66 L3 + Obsidian 验证 2026-09-23](prisIr-companion-m364-5scenarios-critical.md) — **本地完成 A/B1/B2**:Obsidian fcontent_root 死路径→真 vault 修复 + index 重建(10→15 entries) + ai_done 实测;4 个 baseline + 4 个 v3_critical.py + 8 个 v3 jsonl(280-480 条/场景,critical 23-39%);**B3/C 阻塞**:aliyun 192.220.14.165:49108 /workspace 被回收 / 无 nvidia,等用户重启 T4 后训 4 个 v3_conf + 8 个 AgentJev head- [PrisirAI Skills 工作台 Phase 6 主面板 ship](prisIr-skills-workbench-phase-6.md) — 2026-09-28 commit a7830eb;integration.py + 3 HTTP 端点 + polling + 弹卡;puppeteer 实测图
- [PrisirAI Skills 工作台 Phase 7 紧凑化 + 默认全开 ship](prisIr-skills-workbench-phase-7.md) — 2026-09-28;12882→7993c(-38%);去 emoji + name 截断 24 + tags 上限 4;主面板/companion 默认全开
- [PrisirAI Skills 工作台 文档 ship](prisIr-skills-workbench-docs.md) — 2026-09-28;config.md 11 开关 × 2 入口 + 4 档风险门 + fail-open 6 点 + token 经济性表;shipped.md 8 阶段 12 commit + 148 测试
- [PrisirAI Skills 工作台 Phase 8 tier 分层字段 ship](prisIr-skills-workbench-phase-8.md) — 2026-09-28 commit 04ce490;69 skill 标 hot=3/warm=56/cold=10/archive=0;不动能力只分层;capability `_tier` override 启发式;未来留口子按 tier 分层注入
- [OpenMontage 调研 + 借鉴决策](prisIr-openmontage-recon.md) — 2026-09-28;C(借鉴不嵌入)+ 免费资源;5 设计模式(checkpoint/scoring/pre-compose/post-render/budget);5 phase ship 路径估 1-2 周;单集 60 秒稳定后能力 vs 现状对比
- [Phase 9 OM-P1 + MA-P1 双线 ship](prisIr-phase-9-om-p1-and-ma-p1.md) — 2026-09-28 commit 8653940;video_checkpoint + agent_handoff + workflow 集成 + 14/14 测绿;最小 agent 团队示例(Triage→Creative→Art)链式 handoff;累计测试 170
- [Phase 10 OM-P2 Provider 7 维度评分 ship](prisIr-phase-10-om-p2-scoring.md) — 2026-09-28 commit 721184d;video_provider_scoring.py + 16 provider + pick_best + video_creator.pick_provider_for_creator hook;用户「spawn on-demand 架构」决策(provider 不绑单一,跟最小 agent 团队一致);13/13 测绿;累计测试 183
- [Phase 11 OM-P3 Pre-compose 预算校验 ship](prisIr-phase-11-om-p3-pre-compose.md) — 2026-09-28;用户「免费优先,单集 ≤ $0.10」拍板;video_budget.py + check_budget + suggest_replacements + run_workflow pre_compose hook;14/14 测绿;累计测试 197
