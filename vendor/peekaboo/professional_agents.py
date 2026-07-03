# -*- coding: utf-8 -*-
"""
Peekaboo-W Professional Agents - Extended Roles
Phase 4-8 Core Module - Specialized Agent Definitions

Extended Agent Architecture:
    ┌─────────────────────────────────────────────────────────────────┐
    │                   Professional Agent System                   │
    │              (20+ Specialized Agents with Deep Capabilities) │
    └───────────────────────────┬─────────────────────────────────┘
                                │
    ┌───────────────────────────┼─────────────────────────────────┐
    │                           │                                 │
    ▼                           ▼                                 ▼
┌────────────────┐      ┌────────────────┐              ┌────────────────┐
│ Research       │      │ Creative       │              │ Technical      │
│ (研究类)        │      │ (创意类)        │              │ (技术类)        │
│ 5 agents       │      │ 5 agents       │              │ 5 agents       │
└────────────────┘      └────────────────┘              └────────────────┘
                                │
    ┌───────────────────────────┼─────────────────────────────────┐
    │                           │                                 │
    ▼                           ▼                                 ▼
┌────────────────┐      ┌────────────────┐              ┌────────────────┐
│ Business       │      │ Utility       │              │ Meta           │
│ (商业类)        │      │ (实用类)        │              │ (系统类)        │
│ 5 agents       │      │ 5 agents       │              │ 5 agents       │
└────────────────┘      └────────────────┘              └────────────────┘

Features:
1. 25+ Professional Agent Definitions
2. Deep Capability Profiles
3. Cross-Agent Collaboration
4. Adaptive Learning
5. Role-Based Task Routing
"""

import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass, field
from abc import ABC, abstractmethod

sys.path.insert(0, str(Path(__file__).parent.parent))


class AgentCategory(Enum):
    """Agent category classification"""
    RESEARCH = "research"           # 研究类
    CREATIVE = "creative"           # 创意类
    TECHNICAL = "technical"         # 技术类
    BUSINESS = "business"           # 商业类
    UTILITY = "utility"             # 实用类
    META = "meta"                  # 系统类


@dataclass
class Capability:
    """Agent capability definition"""
    name: str
    description: str
    examples: List[str] = field(default_factory=list)
    strength: int = 5  # 1-10 scale


@dataclass
class AgentProfile:
    """Complete agent profile"""
    id: str
    name: str
    name_cn: str  # Chinese name
    category: AgentCategory
    role: str
    description: str
    description_long: str
    capabilities: List[Capability]
    keywords: List[str]
    work_style: str  # How this agent works
    collaboration_patterns: List[str]  # How to collaborate with other agents
    icon: str = "🤖"
    enabled: bool = True
    version: str = "1.0.0"


class ProfessionalAgentRegistry:
    """
    Registry for all professional agents
    Provides discovery and matching capabilities
    """

    def __init__(self):
        self.agents: Dict[str, AgentProfile] = {}
        self._initialize_all_agents()

    def _initialize_all_agents(self):
        """Initialize all 25+ professional agents"""

        # === RESEARCH CATEGORY (5 Agents) ===

        # 1. News Investigator (已有)
        self.agents["news"] = AgentProfile(
            id="news",
            name="News Investigator",
            name_cn="新闻调查员",
            category=AgentCategory.RESEARCH,
            role="Research Journalist",
            description="深度新闻调研与多源对比",
            description_long="专业新闻调查记者，擅长深度调研、多源信息对比、归因分析和事实核查。能够从海量信息中提取关键线索，构建事件时间线，并从政治、经济、社会、技术等多维度分析事件成因。",
            capabilities=[
                Capability("investigation", "深度调查能力", ["新闻事件", "人物背景"], 9),
                Capability("multi_source", "多源信息对比", ["不同媒体报道", "观点对比"], 8),
                Capability("causal_analysis", "归因分析能力", ["原因推断", "影响评估"], 8),
                Capability("fact_check", "事实核查能力", ["信息验证", "谣言粉碎"], 9),
            ],
            keywords=["新闻", "调研", "调查", "事件", "原因", "分析", "investigation"],
            work_style="Search → Analyze → Compare → Synthesize",
            collaboration_patterns=["legal", "finance", "tech"],
            icon="📰"
        )

        # 2. Academic Researcher
        self.agents["academic"] = AgentProfile(
            id="academic",
            name="Academic Researcher",
            name_cn="学术研究员",
            category=AgentCategory.RESEARCH,
            role="Research Scholar",
            description="学术论文辅助与文献综述",
            description_long="专业学术研究助手，擅长文献检索、论文润色、摘要撰写和研究方法设计。帮助研究人员高效完成学术写作任务。",
            capabilities=[
                Capability("literature_review", "文献综述能力", ["文献查找", "总结归纳"], 9),
                Capability("paper_writing", "论文写作能力", ["结构设计", "语言润色"], 8),
                Capability("citation", "引用管理能力", ["参考文献", "引用格式"], 8),
                Capability("research_method", "研究方法设计", ["定量分析", "定性分析"], 7),
            ],
            keywords=["学术", "论文", "研究", "文献", "引用", "academic"],
            work_style="Search → Read → Analyze → Write → Refine",
            collaboration_patterns=["data", "code"],
            icon="🎓"
        )

        # 3. Tech Researcher
        self.agents["tech"] = AgentProfile(
            id="tech",
            name="Tech Researcher",
            name_cn="技术研究员",
            category=AgentCategory.RESEARCH,
            role="Technology Analyst",
            description="技术调研与竞品分析",
            description_long="专业技术调研专家，擅长技术趋势分析、竞品研究和创新评估。为技术决策提供深度洞察。",
            capabilities=[
                Capability("tech_trends", "技术趋势分析", ["新兴技术", "技术路线"], 9),
                Capability("competitor_analysis", "竞品分析能力", ["功能对比", "市场定位"], 8),
                Capability("innovation_assessment", "创新评估能力", ["技术价值", "可行性"], 8),
                Capability("codebase_analysis", "代码库分析", ["架构研究", "代码质量"], 8),
            ],
            keywords=["技术", "调研", "竞品", "趋势", "analysis", "research"],
            work_style="Gather → Analyze → Compare → Report",
            collaboration_patterns=["code", "data"],
            icon="🔬"
        )

        # 4. Legal Analyst
        self.agents["legal"] = AgentProfile(
            id="legal",
            name="Legal Analyst",
            name_cn="法律分析师",
            category=AgentCategory.RESEARCH,
            role="Legal Expert",
            description="法律咨询与合同审查",
            description_long="专业法律分析专家，擅长法规解读、合同审查、合规检查和风险评估。为法律事务提供专业分析。",
            capabilities=[
                Capability("regulation_analysis", "法规解读能力", ["法律条文", "政策解读"], 9),
                Capability("contract_review", "合同审查能力", ["条款分析", "风险识别"], 9),
                Capability("compliance_check", "合规检查能力", ["法规遵循", "合规审计"], 8),
                Capability("risk_assessment", "风险评估能力", ["法律风险", "合规风险"], 8),
            ],
            keywords=["法律", "合同", "合规", "法规", "legal", "contract"],
            work_style="Review → Analyze → Identify Risks → Report",
            collaboration_patterns=["news", "finance"],
            icon="⚖️"
        )

        # 5. Data Analyst
        self.agents["data"] = AgentProfile(
            id="data",
            name="Data Analyst",
            name_cn="数据分析师",
            category=AgentCategory.RESEARCH,
            role="Data Scientist",
            description="数据分析与可视化",
            description_long="专业数据分析师，擅长数据清洗、统计分析、可视化展示和洞察发现。为数据驱动决策提供支持。",
            capabilities=[
                Capability("data_cleaning", "数据清洗能力", ["缺失值处理", "异常值检测"], 9),
                Capability("statistical_analysis", "统计分析能力", ["描述统计", "推断统计"], 9),
                Capability("visualization", "数据可视化能力", ["图表制作", "Dashboard"], 8),
                Capability("insight_discovery", "洞察发现能力", ["模式识别", "趋势发现"], 8),
            ],
            keywords=["数据", "分析", "统计", "可视化", "data", "analytics"],
            work_style="Collect → Clean → Analyze → Visualize → Interpret",
            collaboration_patterns=["code", "academic"],
            icon="📊"
        )

        # === CREATIVE CATEGORY (5 Agents) ===

        # 6. Creative Writer
        self.agents["creative"] = AgentProfile(
            id="creative",
            name="Creative Writer",
            name_cn="创意作家",
            category=AgentCategory.CREATIVE,
            role="Content Creator",
            description="创意写作与内容创作",
            description_long="专业创意写作专家，擅长故事构思、内容创作、文案撰写和创意头脑风暴。激发创意灵感，产出优质内容。",
            capabilities=[
                Capability("storytelling", "故事构思能力", ["情节设计", "人物塑造"], 9),
                Capability("content_creation", "内容创作能力", ["文章撰写", "内容策划"], 9),
                Capability("brainstorming", "头脑风暴能力", ["创意发散", "灵感激发"], 8),
                Capability("copywriting", "文案撰写能力", ["广告文案", "营销文案"], 8),
            ],
            keywords=["创意", "写作", "内容", "故事", "creative", "writing"],
            work_style="Brainstorm → Draft → Refine → Polish",
            collaboration_patterns=["news", "tech"],
            icon="✍️"
        )

        # 7. UI/UX Designer
        self.agents["uiux"] = AgentProfile(
            id="uiux",
            name="UI/UX Designer",
            name_cn="UI/UX设计师",
            category=AgentCategory.CREATIVE,
            role="Design Expert",
            description="界面设计与用户体验优化",
            description_long="专业UI/UX设计师，擅长界面设计、用户体验研究、原型制作和设计系统构建。打造出色的用户体验。",
            capabilities=[
                Capability("ui_design", "界面设计能力", ["视觉设计", "布局设计"], 9),
                Capability("ux_research", "用户体验研究", ["用户调研", "需求分析"], 8),
                Capability("prototyping", "原型制作能力", ["交互原型", "高保真原型"], 9),
                Capability("design_system", "设计系统构建", ["组件库", "设计规范"], 8),
            ],
            keywords=["UI", "UX", "设计", "界面", "原型", "design", "prototype"],
            work_style="Research → Ideate → Design → Prototype → Test",
            collaboration_patterns=["code", "creative"],
            icon="🎨"
        )

        # 8. PPT Creator
        self.agents["ppt"] = AgentProfile(
            id="ppt",
            name="PPT Creator",
            name_cn="PPT制作师",
            category=AgentCategory.CREATIVE,
            role="Presentation Designer",
            description="专业PPT设计与制作",
            description_long="专业PPT设计师，擅长演示文稿设计、内容组织和视觉呈现。让你的演讲更具说服力。",
            capabilities=[
                Capability("slide_design", "幻灯片设计", ["视觉设计", "动画效果"], 9),
                Capability("content_organization", "内容组织能力", ["信息架构", "逻辑结构"], 9),
                Capability("template_creation", "模板创建能力", ["风格设计", "模板库"], 8),
                Capability("data_visualization", "数据可视化", ["图表选择", "数据呈现"], 8),
            ],
            keywords=["PPT", "演示", "幻灯片", "设计", "presentation"],
            work_style="Outline → Design → Content → Refine",
            collaboration_patterns=["data", "finance", "business"],
            icon="📊"
        )

        # 9. Video Script Writer
        self.agents["video"] = AgentProfile(
            id="video",
            name="Video Script Writer",
            name_cn="视频脚本作家",
            category=AgentCategory.CREATIVE,
            role="Multimedia Creator",
            description="视频脚本与多媒体内容创作",
            description_long="专业视频内容创作者，擅长短视频脚本、直播话术、视频剪辑指导和多媒体内容策划。",
            capabilities=[
                Capability("script_writing", "脚本撰写能力", ["分镜设计", "台词撰写"], 9),
                Capability("short_video", "短视频创作", ["抖音/B站脚本", "爆款文案"], 9),
                Capability("live_script", "直播话术设计", ["开场话术", "促单话术"], 8),
                Capability("content_planning", "内容策划能力", ["选题策划", "内容矩阵"], 8),
            ],
            keywords=["视频", "脚本", "直播", "短视频", "video", "script"],
            work_style="Topic → Outline → Script → Review",
            collaboration_patterns=["creative", "uiux"],
            icon="🎬"
        )

        # 10. Brand Strategist
        self.agents["brand"] = AgentProfile(
            id="brand",
            name="Brand Strategist",
            name_cn="品牌策略师",
            category=AgentCategory.CREATIVE,
            role="Marketing Expert",
            description="品牌定位与营销策略",
            description_long="专业品牌策略专家，擅长品牌定位、形象设计、营销策略和传播规划。打造强势品牌。",
            capabilities=[
                Capability("brand_positioning", "品牌定位能力", ["市场定位", "差异化"], 9),
                Capability("brand_identity", "品牌形象设计", ["视觉识别", "品牌故事"], 8),
                Capability("marketing_strategy", "营销策略能力", ["营销组合", "推广计划"], 9),
                Capability("communication_planning", "传播规划能力", ["传播策略", "媒介选择"], 8),
            ],
            keywords=["品牌", "营销", "策略", "定位", "brand", "marketing"],
            work_style="Research → Position → Strategy → Plan → Execute",
            collaboration_patterns=["creative", "business"],
            icon="📣"
        )

        # === TECHNICAL CATEGORY (5 Agents) ===

        # 11. Code Developer
        self.agents["code"] = AgentProfile(
            id="code",
            name="Code Developer",
            name_cn="代码开发者",
            category=AgentCategory.TECHNICAL,
            role="Software Engineer",
            description="代码生成与开发",
            description_long="专业软件开发工程师，擅长代码生成、调试优化、功能实现和架构设计。全栈开发能力。",
            capabilities=[
                Capability("code_generation", "代码生成能力", ["多语言支持", "框架使用"], 10),
                Capability("debugging", "调试排错能力", ["问题定位", "修复方案"], 9),
                Capability("refactoring", "代码重构能力", ["优化重构", "性能提升"], 8),
                Capability("architecture", "架构设计能力", ["系统设计", "模块划分"], 8),
            ],
            keywords=["代码", "编程", "开发", "开发", "code", "programming", "develop"],
            work_style="Analyze → Design → Implement → Test → Optimize",
            collaboration_patterns=["tech", "data"],
            icon="💻"
        )

        # 12. Code Reviewer
        self.agents["review"] = AgentProfile(
            id="review",
            name="Code Reviewer",
            name_cn="代码审查员",
            category=AgentCategory.TECHNICAL,
            role="Quality Assurance",
            description="代码审查与质量保证",
            description_long="专业代码审查专家，擅长代码质量评估、最佳实践建议、安全检查和性能优化。提升代码质量。",
            capabilities=[
                Capability("code_review", "代码审查能力", ["代码检查", "问题识别"], 10),
                Capability("best_practices", "最佳实践建议", ["规范检查", "建议提供"], 9),
                Capability("security_check", "安全检查能力", ["漏洞检测", "安全加固"], 9),
                Capability("performance_review", "性能审查能力", ["性能分析", "优化建议"], 8),
            ],
            keywords=["审查", "代码review", "质量", "安全", "review", "quality"],
            work_style="Review → Analyze → Report → Suggest",
            collaboration_patterns=["code", "devops"],
            icon="🔍"
        )

        # 13. DevOps Engineer
        self.agents["devops"] = AgentProfile(
            id="devops",
            name="DevOps Engineer",
            name_cn="DevOps工程师",
            category=AgentCategory.TECHNICAL,
            role="Infrastructure Expert",
            description="DevOps与自动化部署",
            description_long="专业DevOps工程师，擅长CI/CD流水线、Docker容器化、K8s编排和自动化运维。构建高效运维体系。",
            capabilities=[
                Capability("ci_cd", "CI/CD流水线", ["Jenkins", "GitLab CI"], 9),
                Capability("containerization", "容器化能力", ["Docker", "镜像管理"], 9),
                Capability("orchestration", "编排能力", ["Kubernetes", "服务编排"], 8),
                Capability("infrastructure", "基础设施代码", ["Terraform", "Ansible"], 8),
            ],
            keywords=["DevOps", "部署", "Docker", "K8s", "CI/CD", "devops"],
            work_style="Build → Containerize → Orchestrate → Automate",
            collaboration_patterns=["code", "review"],
            icon="🐳"
        )

        # 14. QA Tester
        self.agents["qa"] = AgentProfile(
            id="qa",
            name="QA Tester",
            name_cn="测试工程师",
            category=AgentCategory.TECHNICAL,
            role="Quality Engineer",
            description="自动化测试与质量保证",
            description_long="专业测试工程师，擅长测试策略制定、自动化测试开发和Bug追踪管理。保障软件质量。",
            capabilities=[
                Capability("test_strategy", "测试策略能力", ["测试计划", "风险评估"], 9),
                Capability("automation_testing", "自动化测试能力", ["Selenium", "Pytest"], 9),
                Capability("bug_tracking", "Bug追踪能力", ["问题管理", "回归测试"], 8),
                Capability("performance_testing", "性能测试能力", ["LoadRunner", "JMeter"], 8),
            ],
            keywords=["测试", "QA", "自动化", "Bug", "testing", "automation"],
            work_style="Plan → Automate → Execute → Track → Report",
            collaboration_patterns=["code", "devops"],
            icon="🧪"
        )

        # 15. Database Expert
        self.agents["db"] = AgentProfile(
            id="db",
            name="Database Expert",
            name_cn="数据库专家",
            category=AgentCategory.TECHNICAL,
            role="Data Engineer",
            description="数据库设计与优化",
            description_long="专业数据库专家，擅长数据库设计、SQL优化、数据迁移和性能调优。打造高效数据存储。",
            capabilities=[
                Capability("db_design", "数据库设计能力", ["ER图", "范式设计"], 10),
                Capability("sql_optimization", "SQL优化能力", ["查询优化", "索引优化"], 9),
                Capability("data_migration", "数据迁移能力", ["ETL", "数据同步"], 8),
                Capability("performance_tuning", "性能调优能力", ["配置优化", "慢查询分析"], 9),
            ],
            keywords=["数据库", "SQL", "优化", "迁移", "database", "sql"],
            work_style="Design → Implement → Optimize → Monitor",
            collaboration_patterns=["code", "data"],
            icon="🗄️"
        )

        # === BUSINESS CATEGORY (5 Agents) ===

        # 16. Financial Analyst
        self.agents["finance"] = AgentProfile(
            id="finance",
            name="Financial Analyst",
            name_cn="金融分析师",
            category=AgentCategory.BUSINESS,
            role="Financial Expert",
            description="财报分析与投资评估",
            description_long="专业金融分析师，擅长财务报表分析、投资估值、风险评估和市场研究。专业投资决策支持。",
            capabilities=[
                Capability("financial_analysis", "财务分析能力", ["报表分析", "指标计算"], 10),
                Capability("valuation", "估值能力", ["DCF", "相对估值"], 9),
                Capability("risk_assessment", "风险评估能力", ["市场风险", "信用风险"], 8),
                Capability("market_research", "市场研究能力", ["行业分析", "竞争分析"], 8),
            ],
            keywords=["金融", "财务", "投资", "分析", "finance", "investment"],
            work_style="Collect → Analyze → Valuate → Report",
            collaboration_patterns=["data", "legal"],
            icon="💰"
        )

        # 17. Business Analyst
        self.agents["business"] = AgentProfile(
            id="business",
            name="Business Analyst",
            name_cn="商业分析师",
            category=AgentCategory.BUSINESS,
            role="Strategy Consultant",
            description="商业分析与策略规划",
            description_long="专业商业分析师，擅长商业模式分析、竞争策略制定、市场机会识别和商业计划书撰写。",
            capabilities=[
                Capability("business_model", "商业模式分析", ["画布分析", "盈利模式"], 9),
                Capability("competitive_strategy", "竞争策略能力", ["五力分析", "SWOT"], 9),
                Capability("market_opportunity", "市场机会识别", ["市场定位", "机会评估"], 8),
                Capability("bp_writing", "商业计划书撰写", ["BP撰写", "路演材料"], 9),
            ],
            keywords=["商业", "策略", "分析", "市场", "business", "strategy"],
            work_style="Research → Analyze → Strategize → Document",
            collaboration_patterns=["finance", "brand"],
            icon="💼"
        )

        # 18. Project Manager
        self.agents["pm"] = AgentProfile(
            id="pm",
            name="Project Manager",
            name_cn="项目经理",
            category=AgentCategory.BUSINESS,
            role="Project Leader",
            description="项目规划与进度管理",
            description_long="专业项目经理，擅长项目规划、进度管理、风险控制和团队协调。确保项目成功交付。",
            capabilities=[
                Capability("project_planning", "项目规划能力", ["WBS分解", "里程碑"], 10),
                Capability("schedule_management", "进度管理能力", ["进度跟踪", "偏差分析"], 9),
                Capability("risk_control", "风险控制能力", ["风险识别", "应对策略"], 9),
                Capability("team_coordination", "团队协调能力", ["资源分配", "沟通管理"], 8),
            ],
            keywords=["项目", "管理", "规划", "进度", "project", "management"],
            work_style="Plan → Execute → Monitor → Control → Close",
            collaboration_patterns=["code", "qa"],
            icon="📋"
        )

        # 19. Sales Expert
        self.agents["sales"] = AgentProfile(
            id="sales",
            name="Sales Expert",
            name_cn="销售专家",
            category=AgentCategory.BUSINESS,
            role="Sales Strategist",
            description="销售策略与客户管理",
            description_long="专业销售专家，擅长销售策略制定、客户关系管理和销售话术优化。提升销售业绩。",
            capabilities=[
                Capability("sales_strategy", "销售策略能力", ["目标设定", "策略制定"], 9),
                Capability("crm", "客户关系管理", ["客户维护", "需求挖掘"], 9),
                Capability("sales_script", "销售话术能力", ["开场白", "异议处理"], 10),
                Capability("negotiation", "谈判能力", ["价格谈判", "合同谈判"], 9),
            ],
            keywords=["销售", "客户", "谈判", "话术", "sales", "crm"],
            work_style="Research → Approach → Present → Negotiate → Close",
            collaboration_patterns=["business", "brand"],
            icon="🤝"
        )

        # 20. HR Specialist
        self.agents["hr"] = AgentProfile(
            id="hr",
            name="HR Specialist",
            name_cn="人力资源专家",
            category=AgentCategory.BUSINESS,
            role="HR Expert",
            description="人才招聘与组织发展",
            description_long="专业人力资源专家，擅长人才招聘、绩效管理、培训发展和组织文化建设。打造高效团队。",
            capabilities=[
                Capability("recruitment", "招聘能力", ["JD撰写", "面试设计"], 10),
                Capability("performance_mgmt", "绩效管理能力", ["KPI设计", "评估体系"], 9),
                Capability("training", "培训发展能力", ["培训设计", "课程开发"], 8),
                Capability("org_culture", "组织文化建设", ["价值观", "氛围营造"], 8),
            ],
            keywords=["HR", "招聘", "绩效", "培训", "hr", "recruitment"],
            work_style="Assess → Recruit → Develop → Evaluate → Optimize",
            collaboration_patterns=["pm", "business"],
            icon="👥"
        )

        # === UTILITY CATEGORY (5 Agents) ===

        # 21. Personal Assistant
        self.agents["assistant"] = AgentProfile(
            id="assistant",
            name="Personal Assistant",
            name_cn="私人助理",
            category=AgentCategory.UTILITY,
            role="Productivity Expert",
            description="日常任务管理与效率提升",
            description_long="高效私人助理，擅长日程管理、任务提醒、信息整理和邮件处理。提升日常效率。",
            capabilities=[
                Capability("schedule_mgmt", "日程管理能力", ["日历管理", "会议安排"], 10),
                Capability("task_reminder", "任务提醒能力", ["待办管理", "提醒设置"], 10),
                Capability("info_organization", "信息整理能力", ["笔记整理", "资料归档"], 9),
                Capability("email_mgmt", "邮件处理能力", ["邮件分类", "快速回复"], 9),
            ],
            keywords=["助理", "日程", "任务", "效率", "assistant", "schedule"],
            work_style="Capture → Organize → Execute → Review",
            collaboration_patterns=["pm", "news"],
            icon="📅"
        )

        # 22. Translator
        self.agents["translator"] = AgentProfile(
            id="translator",
            name="Translator",
            name_cn="翻译专家",
            category=AgentCategory.UTILITY,
            role="Language Expert",
            description="专业翻译与语言服务",
            description_long="专业翻译专家，擅长中英互译、专业术语处理和文化适应。提供高质量语言服务。",
            capabilities=[
                Capability("translation", "翻译能力", ["中英互译", "多语言"], 10),
                Capability("localization", "本地化能力", ["文化适应", "区域化"], 9),
                Capability("terminology", "专业术语处理", ["行业术语", "术语库"], 9),
                Capability("proofreading", "校对能力", ["语法检查", "风格校对"], 9),
            ],
            keywords=["翻译", "语言", "本地化", "translation", "language"],
            work_style="Analyze → Translate → Review → Polish",
            collaboration_patterns=["academic", "creative"],
            icon="🌍"
        )

        # 23. Knowledge Manager
        self.agents["knowledge"] = AgentProfile(
            id="knowledge",
            name="Knowledge Manager",
            name_cn="知识管理专家",
            category=AgentCategory.UTILITY,
            role="Knowledge Engineer",
            description="知识整理与知识库构建",
            description_long="专业知识管理专家，擅长知识整理、知识库构建、信息检索和知识图谱构建。打造组织知识资产。",
            capabilities=[
                Capability("knowledge_organization", "知识整理能力", ["分类体系", "标签系统"], 10),
                Capability("knowledge_base", "知识库构建能力", ["文档管理", "检索系统"], 9),
                Capability("info_retrieval", "信息检索能力", ["搜索优化", "知识发现"], 9),
                Capability("knowledge_graph", "知识图谱能力", ["实体关系", "知识推理"], 8),
            ],
            keywords=["知识", "知识库", "整理", "knowledge", "wiki"],
            work_style="Collect → Organize → Index → Retrieve → Share",
            collaboration_patterns=["assistant", "data"],
            icon="📚"
        )

        # 24. Research Assistant (General)
        self.agents["research"] = AgentProfile(
            id="research",
            name="Research Assistant",
            name_cn="研究助理",
            category=AgentCategory.UTILITY,
            role="Research Helper",
            description="通用调研与信息检索",
            description_long="通用调研助理，擅长信息检索、资料收集、报告撰写和信息可视化。辅助各类研究任务。",
            capabilities=[
                Capability("info_retrieval", "信息检索能力", ["搜索技巧", "来源评估"], 9),
                Capability("data_collection", "资料收集能力", ["数据收集", "文献查找"], 9),
                Capability("report_writing", "报告撰写能力", ["结构设计", "内容组织"], 8),
                Capability("info_viz", "信息可视化能力", ["图表制作", "信息图设计"], 8),
            ],
            keywords=["调研", "助理", "信息", "检索", "research", "survey"],
            work_style="Search → Collect → Analyze → Report",
            collaboration_patterns=["news", "data", "academic"],
            icon="🔎"
        )

        # 25. Meeting Assistant
        self.agents["meeting"] = AgentProfile(
            id="meeting",
            name="Meeting Assistant",
            name_cn="会议助理",
            category=AgentCategory.UTILITY,
            role="Meeting Facilitator",
            description="会议组织与纪要整理",
            description_long="专业会议助理，擅长会议组织、议程设计、会议纪要和行动跟踪。提升会议效率。",
            capabilities=[
                Capability("meeting_organization", "会议组织能力", ["日程安排", "邀请发送"], 10),
                Capability("agenda_design", "议程设计能力", ["议题设置", "时间分配"], 9),
                Capability("meeting_notes", "会议纪要能力", ["内容记录", "要点提炼"], 10),
                Capability("action_tracking", "行动跟踪能力", ["任务分配", "进度跟踪"], 9),
            ],
            keywords=["会议", "纪要", "议程", "组织", "meeting", "minutes"],
            work_style="Plan → Organize → Document → Track",
            collaboration_patterns=["pm", "assistant"],
            icon="📝"
        )

        # === META CATEGORY (5 Agents) - System Agents ===

        # 26. Orchestrator (Peekaboo Core)
        self.agents["pecky"] = AgentProfile(
            id="pecky",
            name="Peekaboo Orchestrator",
            name_cn="布布调度员",
            category=AgentCategory.META,
            role="System Coordinator",
            description="浏览器自动化与记忆管理",
            description_long="Peekaboo核心调度员，擅长浏览器自动化、文章收藏、Obsidian同步和记忆管理。是系统的核心协调者。",
            capabilities=[
                Capability("browser_automation", "浏览器自动化能力", ["点击", "输入", "截图"], 10),
                Capability("article_archive", "文章存档能力", ["Web Clipper", "内容提取"], 10),
                Capability("obsidian_sync", "Obsidian同步能力", ["笔记同步", "双向更新"], 9),
                Capability("memory_mgmt", "记忆管理能力", ["存储", "检索", "共享"], 9),
            ],
            keywords=["布布", "浏览器", "自动化", "记忆", "pecky", "browser"],
            work_style="Detect → Automate → Archive → Sync → Remember",
            collaboration_patterns=["news", "knowledge", "assistant"],
            icon="🤖"
        )

        # 27. Team Leader
        self.agents["leader"] = AgentProfile(
            id="leader",
            name="Team Leader",
            name_cn="团队领导",
            category=AgentCategory.META,
            role="Team Orchestrator",
            description="团队任务分解与协调",
            description_long="专业团队领导，擅长任务分解、团队协调、结果汇总和进度跟踪。领导团队高效完成任务。",
            capabilities=[
                Capability("task_decomposition", "任务分解能力", ["子任务划分", "依赖分析"], 10),
                Capability("team_coordination", "团队协调能力", ["角色分配", "进度跟踪"], 10),
                Capability("result_synthesis", "结果汇总能力", ["成果整合", "报告生成"], 9),
                Capability("conflict_resolution", "冲突解决能力", ["意见协调", "决策推进"], 9),
            ],
            keywords=["领导", "团队", "协调", "leader", "team"],
            work_style="Analyze → Decompose → Coordinate → Synthesize → Report",
            collaboration_patterns=["all_agents"],
            icon="👑"
        )

        # 28. MCP Manager
        self.agents["mcp"] = AgentProfile(
            id="mcp",
            name="MCP Manager",
            name_cn="MCP管理器",
            category=AgentCategory.META,
            role="Tool Coordinator",
            description="MCP工具统一管理与调度",
            description_long="MCP工具管理器，统一管理所有工具的注册、发现、调度和执行。为其他Agent提供工具支持。",
            capabilities=[
                Capability("tool_registry", "工具注册能力", ["工具注册", "元数据管理"], 10),
                Capability("tool_discovery", "工具发现能力", ["能力匹配", "智能推荐"], 9),
                Capability("tool_scheduling", "工具调度能力", ["执行调度", "资源分配"], 9),
                Capability("tool_execution", "工具执行能力", ["统一执行", "结果处理"], 10),
            ],
            keywords=["MCP", "工具", "管理", "调度", "mcp", "tools"],
            work_style="Register → Discover → Schedule → Execute → Report",
            collaboration_patterns=["leader", "pecky"],
            icon="🔧"
        )

        # 29. YOLO Controller
        self.agents["yolo"] = AgentProfile(
            id="yolo",
            name="YOLO Controller",
            name_cn="YOLO控制器",
            category=AgentCategory.META,
            role="Autonomous Controller",
            description="全自动任务执行控制",
            description_long="YOLO模式控制器，实现全自动任务分解、Agent选择、执行和结果收集。无人干预完成任务。",
            capabilities=[
                Capability("auto_planning", "自动规划能力", ["任务理解", "规划生成"], 10),
                Capability("agent_selection", "Agent选择能力", ["智能匹配", "角色分配"], 10),
                Capability("auto_execution", "自动执行能力", ["任务执行", "状态监控"], 10),
                Capability("result_collection", "结果收集能力", ["成果汇总", "报告生成"], 9),
            ],
            keywords=["YOLO", "自动", "自主", "无人干预", "yolo", "auto"],
            work_style="Understand → Plan → Execute → Collect → Report (Fully Autonomous)",
            collaboration_patterns=["leader", "all_agents"],
            icon="🎯"
        )

        # 30. Health Monitor
        self.agents["health"] = AgentProfile(
            id="health",
            name="Health Monitor",
            name_cn="健康监控器",
            category=AgentCategory.META,
            role="System Health Checker",
            description="系统健康诊断与监控",
            description_long="系统健康监控专家，持续监控系统状态、资源使用、错误日志和性能指标。保障系统稳定运行。",
            capabilities=[
                Capability("health_check", "健康检查能力", ["状态检测", "指标采集"], 10),
                Capability("error_detection", "错误检测能力", ["日志分析", "异常识别"], 10),
                Capability("performance_monitoring", "性能监控能力", ["资源使用", "性能分析"], 9),
                Capability("alert_generation", "告警生成能力", ["阈值设置", "告警通知"], 9),
            ],
            keywords=["健康", "监控", "诊断", "health", "monitor", "status"],
            work_style="Monitor → Detect → Alert → Report",
            collaboration_patterns=["leader", "pecky"],
            icon="🏥"
        )

        print(f"[REGISTRY] Initialized {len(self.agents)} professional agents")

    def get_agent(self, agent_id: str) -> Optional[AgentProfile]:
        """Get agent by ID"""
        return self.agents.get(agent_id)

    def list_agents(self, category: AgentCategory = None) -> List[AgentProfile]:
        """List agents, optionally filtered by category"""
        if category:
            return [a for a in self.agents.values() if a.category == category]
        return list(self.agents.values())

    def find_agents(self, query: str) -> List[AgentProfile]:
        """Find agents by query"""
        query_lower = query.lower()
        results = []

        for agent in self.agents.values():
            # Match keywords
            if any(query_lower in kw.lower() for kw in agent.keywords):
                results.append(agent)
                continue

            # Match name
            if query_lower in agent.name.lower() or query_lower in agent.name_cn.lower():
                results.append(agent)
                continue

            # Match role
            if query_lower in agent.role.lower():
                results.append(agent)

        return results

    def get_categories(self) -> Dict[AgentCategory, List[AgentProfile]]:
        """Get agents grouped by category"""
        result = {cat: [] for cat in AgentCategory}

        for agent in self.agents.values():
            result[agent.category].append(agent)

        return result

    def get_agent_count_by_category(self) -> Dict[str, int]:
        """Get agent count by category"""
        counts = {}
        for cat in AgentCategory:
            counts[cat.value] = len([a for a in self.agents.values() if a.category == cat])
        return counts


# Singleton accessor
def get_agent_registry() -> ProfessionalAgentRegistry:
    """Get or create Professional Agent Registry singleton"""
    if not hasattr(get_agent_registry, "_instance"):
        get_agent_registry._instance = ProfessionalAgentRegistry()
    return get_agent_registry._instance


# CLI Interface
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Peekaboo-W Professional Agents")
    sub = parser.add_subparsers(dest="cmd")

    # List all agents
    list_cmd = sub.add_parser("list", help="List all agents")
    list_cmd.add_argument("--category", "-c", help="Filter by category")

    # Show agent details
    agent_cmd = sub.add_parser("agent", help="Show agent details")
    agent_cmd.add_argument("name", help="Agent name or ID")

    # Search agents
    search_cmd = sub.add_parser("search", help="Search agents")
    search_cmd.add_argument("query", help="Search query")

    # Categories summary
    sub.add_parser("categories", help="Show categories summary")

    # Statistics
    sub.add_parser("stats", help="Show agent statistics")

    args = parser.parse_args()

    registry = get_agent_registry()

    if args.cmd == "list":
        category = None
        if args.category:
            try:
                category = AgentCategory(args.category)
            except ValueError:
                pass

        agents = registry.list_agents(category)
        print(f""
🤖 Professional Agents ({len(agents)})")
        print("="*60)

        if not category:
            # Group by category
            by_cat = registry.get_categories()
            for cat, cat_agents in by_cat.items():
                if cat_agents:
                    print(f""
【{cat.value.upper()}】")
                    for agent in cat_agents:
                        print(f"  {agent.icon} {agent.name} ({agent.name_cn})")
        else:
            for agent in agents:
                print(f"  {agent.icon} {agent.name} ({agent.name_cn})")
                print(f"     {agent.description}")

    elif args.cmd == "agent":
        agent = registry.get_agent(args.name)
        if not agent:
            # Try searching
            results = registry.find_agents(args.name)
            if results:
                agent = results[0]

        if agent:
            print(f""
{'='*60}")
            print(f"{agent.icon} {agent.name} ({agent.name_cn})")
            print(f"{'='*60}")
            print(f"ID: {agent.id}")
            print(f"Category: {agent.category.value}")
            print(f"Role: {agent.role}")
            print(f""
Description:
{agent.description_long}")
            print(f""
Work Style: {agent.work_style}")
            print(f""
Keywords: {', '.join(agent.keywords)}")
            print(f""
Capabilities:")
            for cap in agent.capabilities:
                print(f"  📌 {cap.name} (Strength: {cap.strength}/10)")
                print(f"     {cap.description}")
            print(f""
Collaboration: {', '.join(agent.collaboration_patterns)}")
        else:
            print(f"[ERROR] Agent not found: {args.name}")

    elif args.cmd == "search":
        results = registry.find_agents(args.query)
        print(f""
🔍 Search results for '{args.query}': {len(results)} agents")
        for agent in results:
            print(f"  {agent.icon} {agent.name} - {agent.description}")

    elif args.cmd == "categories":
        counts = registry.get_agent_count_by_category()
        print(f""
📊 Agent Categories Summary")
        print("="*60)
        for cat, count in counts.items():
            if count > 0:
                print(f"  【{cat.upper()}】 {count} agents")

    elif args.cmd == "stats":
        total = len(registry.agents)
        counts = registry.get_agent_count_by_category()
        print(f""
📊 Agent Statistics")
        print("="*60)
        print(f"Total Agents: {total}")
        print(""
By Category:")
        for cat, count in counts.items():
            if count > 0:
                print(f"  - {cat}: {count}")

    else:
        parser.print_help()