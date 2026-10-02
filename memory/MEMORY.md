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
- [Phase 11-H 国内 CNY provider + ¥100/集预算 ship](prisIr-phase-11h-cny-budget-update.md) — 2026-09-28;用户「国内短剧成本很高」拍板;6 国内 provider + 全转 USD(CNY×0.139)+ 预算改 $14;真实国内 11 步编排 $0.54 < $14;suggest_replacements bug 修复;14/14 原 + 8/8 H 测试全绿;累计测试 211
- [Phase 12 OM-P4 免费资源真集成 ship](prisIr-phase-12-om-p4-free-resources.md) — 2026-09-28 commit;用户「渐进 ship + 证据」决策;edge_tts_client + pixabay_client + archive_org_client + free_resource_fetcher;edge-tts 真生成 24KB 中文 mp3 / archive.org 真搜 3 hits / Pixabay 真探测 400;13/13 测绿(含 4 次真调用);累计测试 224
- [Phase 12b OM-P4 Pixabay 修复 ship](prisIr-phase-12b-om-p4-pixabay-fix.md) — 2026-09-28;用户配 PIXABAY_API_KEY 后实测发现:Pixabay **无音频 API**(/api/audio/ 403,我之前 search_music 瞎编);删 search_music + 加 search_images + probe_key 升级(rate_limit 100/60s);free_bgm 改走 archive.org audio;pixabay_music scoring availability=0 自动落到 fma_music;18/18 测绿(6 项真调);累计测试 229
- [Pixabay API 实际覆盖范围](pixabay-no-audio-api.md) — reference:网页有 Music/Photos/Videos 多分类,但公开 REST API 只 images+videos;Music **只能手动下**(API 不开放);100/60s 速率;per_page 最小 3;BGM 真集成走 archive.org audio
- [M3.66 dropdown category 分组](prisIr-m366-dropdown-category.md) — 2026-09-28 ship;用户「同款 dropdown 清晰区分」决策;`companion_llm_providers.py` 加 `category` 字段(5 类:llm/tts/image/music/video)+ 22 个新 spec;LLM 写 keys.db,专业模型写 `~/.prisIrai/media_keys.json/_prisir_key` 子键(防污染顶层 siliconflow 等);原子写;累计 dropdown 平台 15→37

## ECC/claude-swarm 借鉴 P2-Rules + P2-Hooks + P1-Instincts(2026-10-01/02 ship)
- [P2-Rules ship](prisIr-p2-rules-shipped.md) — AGENTS.md frontmatter 解析 + build_messages 注入(完全 ECC 对齐);`prisir_work/rules.py` + 17 测试
- [P2-Hooks ship](prisIr-p2-hooks-shipped.md) — 4 hook(secrets_check/data_egress/mtime_check/noop_user_prompt)+ 3 档 profile(off→standard→strict,默认 off);22 测试
- [P1-Instincts ship](prisIr-p1-instincts-shipped.md) — JSONL 存储 + threshold 0.5 + reinforce ±0.05/0.1;`memory/instincts.py` + 17 测试;累计 56 测试全绿

## jcode 借鉴 P3-HookRisk + P4-Compaction + P5-SwarmTLDR(2026-10-02 ship)
- [P3-HookRisk ship](prisIr-p3-hookrisk-shipped.md) — 借鉴 jcode-command-risk,4 档分级 Safe/Low/Confirm/Catastrophic + 8 Catastrophic 模式(rm -rf / find -delete / shred / truncate / dd of= / `&gt;file` / mkfs / chmod -R 000);`~/.claude/hooks/command_risk.py` + 13 测试;profile 三档(off→standard→strict,默认 off)
- [P4-Compaction ship](prisIr-p4-compaction-shipped.md) — 借鉴 jcode-compaction-core,200K token budget + 80%/95% 双阈值 + IMAGE_TOKEN_COST=1600 平摊 + 中文 4 段 SUMMARY_PROMPT;`memory/compaction.py` + 14 测试;Step 7 退一步只做 95% hard 压缩(无同步 llm_call);build_messages 钩子在 return msgs 之前
- [P5-SwarmTLDR ship](prisIr-p5-swarmtldr-shipped.md) — 借鉴 jcode-swarm-core,SWARM_TLDR_REQUIRED_OVER_CHARS=240 + MAX_SWARM_TLDR_CHARS=200 + SWARM_COMPLETION_REPORT_MARKER + MAX=4000;`dev_dispatch.py` +5 函数 + `prisIragent_dev_consumer.py` line 329-344 完成报告校验(只 log.warning 不阻断)+ 27 测试;累计 110 测试全绿
- [P3j T23 web_search 借 SearXNG 加 87 个无 key 引擎 ship](p3jt23-web-search-multi-engine.md) — 2026-10-02 commit d0db8c1;`prisIr_work/search_engines/` 12 子文件(general/academic/code/wikipedia/news/maps/images/media/specialty)+ _Stats 类 + stats()/reset_stats() API + BanDict 5s→24h 阶梯;provider 总数 9→96;research.py 立即得到学术/维基/代码/视频媒体全覆盖;99 测试全绿(89 parametrize + 5 mock + 5 integration + 4 ban + 9 sanity);3 坑(register_all 内部 import 避循环+pack.object 子模块避 pytest mock 失活+wikipedia 改 _json_get 直 patch)
