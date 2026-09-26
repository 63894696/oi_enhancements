- [HackerNews Algolia 集成 PrisirAI](hn-prisir.md) — 2026-09-26 ship,免 key,hn_search provider 始终注册;56/56 绿(含真探活);Reddit 改换决定
- [Exa MCP 集成 PrisirAI](exa-prisir.md) — 2026-09-26 ship,env 触发 exa_search provider + 4 capability;L0 只读;52/52 绿
- [PrisirAI 间接接入审计](audit-direct-vs-indirect.md) — 2026-09-26 ship,4 处子进程桥(agent-reach/Easel 6/Easel 9/port_registry)全是设计意图,无需直接化;7 项真上游已直接整合
- [GitHub agent 能力层项目调研](agent-tool-survey.md) — 2026-09-26,8 大类 50+ 项目 4 档决策:Playwright MCP P2 推迟 / 其它多不接(重叠 Easel/付费/产品边界)
- [Playwright MCP 集成 PrisirAI](playwright-mcp-prisir.md) — 2026-09-26 ship,stdio JSON-RPC 子进程桥(7 工具),threading.Lock 非可重入坑修复,60/60 绿

## M3.66 classification-head 路径(活跃)
- [M3.66 立项 classification-head 替换路径 2026-09-23](prisIr-companion-m366-classification-head.md) — 8 scenario 用 AgentJev 风格 classification head 替换 15 LoRA;证据:parse_fail 0%/延迟 100x/Brier 5-10x;L1 调研 + L2 aliyun T4 训 demo + L3 8 scenario + L4 双通道 fallback

## M3.64 5 scenario critical 专项 + Obsidian 复活(活跃,本地完成)
- [M3.64 5 scenario critical 专项 + M3.66 L3 + Obsidian 验证 2026-09-23](prisIr-companion-m364-5scenarios-critical.md) — **本地完成 A/B1/B2**:Obsidian fcontent_root 死路径→真 vault 修复 + index 重建(10→15 entries) + ai_done 实测;4 个 baseline + 4 个 v3_critical.py + 8 个 v3 jsonl(280-480 条/场景,critical 23-39%);**B3/C 阻塞**:aliyun 192.220.14.165:49108 /workspace 被回收 / 无 nvidia,等用户重启 T4 后训 4 个 v3_conf + 8 个 AgentJev head