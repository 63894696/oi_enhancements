#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Shared Memory Hub - Agent间记忆共享中心
所有Agent共享的记忆存储层

使用方式:
    from src.shared_memory import SharedMemory, AgentMemory

    # 任何Agent都可以使用
    hub = SharedMemory()

    # 布布存档
    hub.store("pecky", title="文章标题", content="内容", type="article")

    # 记者调研
    hub.store("news", title="调研报告", content="内容", type="research")

    # CodeAgent提取
    refs = hub.retrieve("news", query="相关技术")
"""

import os
import sys
import json
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

sys.path.insert(0, str(Path(__file__).parent))

try:
    from mempalace.searcher import search_memories
    from mempalace.layers import MemoryStack
    MEMPALACE_AVAILABLE = True
except ImportError:
    MEMPALACE_AVAILABLE = False


# 项目路径
PROJECT_DIR = Path(__file__).parent.parent
MEMORY_DIR = PROJECT_DIR / ".memory" / "shared"
METADATA_FILE = MEMORY_DIR / "metadata.json"


class SharedMemory:
    """
    共享记忆中心 - 所有Agent的记忆共享层

    架构:
        ┌─────────────────────────────────────────┐
        │          SharedMemory Hub                │
        │    (Agent间记忆共享中央协调器)           │
        └────────────────┬────────────────────────┘
                         │
        ┌────────────────┼────────────────┬────────────────┐
        │                │                │                │
        ▼                ▼                ▼                ▼
    ┌────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐
    │ 布布   │    │ 记者Agent │    │ CodeAgent │    │ 其他Agent│
    │(pecky) │    │  (news)  │    │  (code)   │    │  (xxx)   │
    └────────┘    └──────────┘    └───────────┘    └──────────┘

    存储类型:
    - article: 布布存档的文章
    - research: 记者调研报告
    - code: CodeAgent生成的代码片段
    - idea: 创意想法
    - fact: 事实/知识点
    """

    # Agent标识
    AGENTS = ["pecky", "news", "code", "creative", "legal", "finance", "tech"]

    # 记忆类型
    TYPES = ["article", "research", "code", "idea", "fact", "note", "summary"]

    def __init__(self, hub_name: str = "peekaboo_hub"):
        self.hub_name = hub_name
        self.memory_dir = MEMORY_DIR
        self._ensure_storage()

    def _ensure_storage(self):
        """确保存储目录存在"""
        self.memory_dir.mkdir(parents=True, exist_ok=True)

        # 初始化元数据
        if not METADATA_FILE.exists():
            metadata = {
                "created": datetime.now().isoformat(),
                "agents": {agent: {"count": 0} for agent in self.AGENTS},
                "types": {t: {"count": 0} for t in self.TYPES}
            }
            self._save_metadata(metadata)

    def _load_metadata(self) -> Dict:
        """加载元数据"""
        if METADATA_FILE.exists():
            with open(METADATA_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    def _save_metadata(self, metadata: Dict):
        """保存元数据"""
        with open(METADATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, ensure_ascii=False, indent=2)

    def _update_metadata(self, agent: str, mem_type: str):
        """更新元数据"""
        metadata = self._load_metadata()

        if agent not in metadata.get("agents", {}):
            metadata.setdefault("agents", {})[agent] = {"count": 0}
        metadata["agents"][agent]["count"] = metadata["agents"].get(agent, {}).get("count", 0) + 1

        if mem_type not in metadata.get("types", {}):
            metadata.setdefault("types", {})[mem_type] = {"count": 0}
        metadata["types"][mem_type]["count"] = metadata["types"].get(mem_type, {}).get("count", 0) + 1

        self._save_metadata(metadata)

    def store(self,
              agent: str,
              title: str,
              content: str,
              mem_type: str = "note",
              tags: Optional[List[str]] = None,
              source_url: str = "",
              metadata: Optional[Dict] = None) -> str:
        """
        存储记忆到共享中心

        Args:
            agent: 来源Agent标识 (pecky/news/code/creative/legal/finance/tech)
            title: 记忆标题
            content: 记忆内容
            mem_type: 记忆类型 (article/research/code/idea/fact/note/summary)
            tags: 标签列表
            source_url: 来源URL
            metadata: 额外元数据

        Returns:
            str: 记忆ID
        """
        # 验证agent
        if agent not in self.AGENTS:
            print(f"[警告] 未知的Agent: {agent}, 允许: {self.AGENTS}")

        # 验证类型
        if mem_type not in self.TYPES:
            print(f"[警告] 未知的类型: {mem_type}, 允许: {self.TYPES}")

        # 生成记忆ID (添加微秒确保唯一性)
        memory_id = f"{agent}_{mem_type}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(datetime.now().microsecond/1000):03d}"

        # 记忆数据
        memory_data = {
            "id": memory_id,
            "agent": agent,
            "title": title,
            "content": content,
            "type": mem_type,
            "tags": tags or [],
            "source_url": source_url,
            "metadata": metadata or {},
            "created_at": datetime.now().isoformat(),
            "accessed_at": datetime.now().isoformat(),
            "access_count": 0
        }

        # 保存文件
        file_path = self.memory_dir / f"{memory_id}.json"
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(memory_data, f, ensure_ascii=False, indent=2)

        # 更新元数据
        self._update_metadata(agent, mem_type)

        print(f"[共享记忆] ✅ 已存储 [{agent}/{mem_type}]: {title[:30]}...")
        return memory_id

    def retrieve(self,
                 query: str,
                 agents: Optional[List[str]] = None,
                 mem_types: Optional[List[str]] = None,
                 limit: int = 10,
                 full_text: bool = True) -> List[Dict]:
        """
        从共享中心检索记忆

        Args:
            query: 搜索关键词
            agents: 限定Agent (None表示所有)
            mem_types: 限定类型 (None表示所有)
            limit: 返回数量限制
            full_text: 是否全文搜索

        Returns:
            List[Dict]: 记忆列表
        """
        results = []

        for file_path in self.memory_dir.glob("*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Agent过滤
                if agents and data.get("agent") not in agents:
                    continue

                # 类型过滤
                if mem_types and data.get("type") not in mem_types:
                    continue

                # 搜索匹配
                query_lower = query.lower()
                title_match = query_lower in data.get("title", "").lower()
                content_match = query_lower in data.get("content", "").lower()
                tag_match = any(query_lower in tag.lower() for tag in data.get("tags", []))

                if title_match or content_match or tag_match:
                    results.append(data)

            except Exception as e:
                print(f"[错误] 读取记忆失败: {file_path}, {e}")

        # 按访问次数和创建时间排序
        results.sort(key=lambda x: (x.get("access_count", 0), x.get("created_at", "")),
                    reverse=True)

        return results[:limit]

    def get_by_agent(self, agent: str, limit: int = 20) -> List[Dict]:
        """获取指定Agent的所有记忆"""
        results = []

        for file_path in self.memory_dir.glob(f"{agent}_*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    results.append(json.load(f))
            except Exception as e:
                print(f"[错误] 读取记忆失败: {file_path}, {e}")

        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results[:limit]

    def get_by_type(self, mem_type: str, limit: int = 20) -> List[Dict]:
        """获取指定类型的所有记忆"""
        results = []

        for file_path in self.memory_dir.glob(f"*_{mem_type}_*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    results.append(json.load(f))
            except Exception as e:
                print(f"[错误] 读取记忆失败: {file_path}, {e}")

        results.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return results[:limit]

    def get_recent(self, limit: int = 20) -> List[Dict]:
        """获取最近的记忆"""
        all_memories = []

        for file_path in self.memory_dir.glob("*.json"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    all_memories.append(json.load(f))
            except Exception:
                pass

        all_memories.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return all_memories[:limit]

    def update_access(self, memory_id: str):
        """更新记忆访问记录"""
        file_path = self.memory_dir / f"{memory_id}.json"

        if file_path.exists():
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            data["accessed_at"] = datetime.now().isoformat()
            data["access_count"] = data.get("access_count", 0) + 1

            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

    def delete(self, memory_id: str) -> bool:
        """删除记忆"""
        file_path = self.memory_dir / f"{memory_id}.json"

        if file_path.exists():
            file_path.unlink()
            print(f"[共享记忆] 🗑️ 已删除: {memory_id}")
            return True

        return False

    def get_stats(self) -> Dict:
        """获取统计信息"""
        metadata = self._load_metadata()

        stats = {
            "total_memories": len(list(self.memory_dir.glob("*.json"))),
            "by_agent": {},
            "by_type": {},
            "recent_activity": self.get_recent(5)
        }

        # 按Agent统计
        for agent in self.AGENTS:
            count = len(list(self.memory_dir.glob(f"{agent}_*.json")))
            stats["by_agent"][agent] = count

        # 按类型统计
        for mem_type in self.TYPES:
            count = len(list(self.memory_dir.glob(f"*_{mem_type}_*.json")))
            stats["by_type"][mem_type] = count

        return stats

    def list_all(self) -> List[str]:
        """列出所有记忆ID"""
        return [f.stem for f in self.memory_dir.glob("*.json")]


class AgentMemory:
    """
    Agent专用记忆接口 - 简化版
    每个Agent使用此类来存储和检索记忆
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self.hub = SharedMemory()

    def save_article(self, title: str, content: str, source_url: str = "",
                     tags: Optional[List[str]] = None):
        """保存文章（布布使用）"""
        return self.hub.store(
            agent=self.agent_name,
            title=title,
            content=content,
            mem_type="article",
            source_url=source_url,
            tags=tags or ["article"]
        )

    def save_research(self, title: str, content: str,
                     tags: Optional[List[str]] = None):
        """保存调研报告（记者使用）"""
        return self.hub.store(
            agent=self.agent_name,
            title=title,
            content=content,
            mem_type="research",
            tags=tags or ["research"]
        )

    def save_code(self, title: str, content: str, language: str = "python",
                  tags: Optional[List[str]] = None):
        """保存代码片段（CodeAgent使用）"""
        return self.hub.store(
            agent=self.agent_name,
            title=title,
            content=content,
            mem_type="code",
            tags=tags or ["code", language]
        )

    def save_idea(self, title: str, content: str,
                  tags: Optional[List[str]] = None):
        """保存创意想法"""
        return self.hub.store(
            agent=self.agent_name,
            title=title,
            content=content,
            mem_type="idea",
            tags=tags or ["idea"]
        )

    def recall(self, query: str, limit: int = 10) -> List[Dict]:
        """检索记忆"""
        return self.hub.retrieve(query, agents=[self.agent_name], limit=limit)

    def get_all(self, limit: int = 50) -> List[Dict]:
        """获取自己的所有记忆"""
        return self.hub.get_by_agent(self.agent_name, limit=limit)

    def get_references(self, topic: str, all_agents: bool = True) -> List[Dict]:
        """
        获取参考记忆（跨Agent）
        CodeAgent开发时可调用此方法获取其他Agent的参考
        """
        agents = None if all_agents else [self.agent_name]
        return self.hub.retrieve(topic, agents=agents, limit=10)


# 便捷函数
def get_shared_memory() -> SharedMemory:
    """获取共享记忆中心实例"""
    return SharedMemory()


def get_pecky_memory() -> AgentMemory:
    """获取布布的记忆接口"""
    return AgentMemory("pecky")


def get_news_memory() -> AgentMemory:
    """获取记者Agent的记忆接口"""
    return AgentMemory("news")


def get_code_memory() -> AgentMemory:
    """获取CodeAgent的记忆接口"""
    return AgentMemory("code")


# CLI入口
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="共享记忆中心 - Agent间记忆管理")
    sub = parser.add_subparsers(dest="cmd")

    # 存储
    store_cmd = sub.add_parser("store", help="存储记忆")
    store_cmd.add_argument("--agent", "-a", required=True, help="Agent名称")
    store_cmd.add_argument("--title", "-t", required=True, help="标题")
    store_cmd.add_argument("--content", "-c", required=True, help="内容")
    store_cmd.add_argument("--type", "-y", default="note", help="类型")
    store_cmd.add_argument("--tags", nargs="*", help="标签")

    # 检索
    search_cmd = sub.add_parser("search", help="检索记忆")
    search_cmd.add_argument("query", help="搜索关键词")
    search_cmd.add_argument("--agents", nargs="*", help="限定Agent")
    search_cmd.add_argument("--types", nargs="*", help="限定类型")
    search_cmd.add_argument("--limit", "-n", type=int, default=10, help="返回数量")

    # 列出
    list_cmd = sub.add_parser("list", help="列出记忆")
    list_cmd.add_argument("--agent", "-a", help="按Agent筛选")
    list_cmd.add_argument("--type", "-y", help="按类型筛选")
    list_cmd.add_argument("--limit", "-n", type=int, default=20, help="返回数量")

    # 统计
    sub.add_parser("stats", help="统计信息")

    # 最近
    recent_cmd = sub.add_parser("recent", help="最近的记忆")
    recent_cmd.add_argument("--limit", "-n", type=int, default=10, help="返回数量")

    args = parser.parse_args()

    hub = SharedMemory()

    if args.cmd == "store":
        memory_id = hub.store(
            agent=args.agent,
            title=args.title,
            content=args.content,
            mem_type=args.type,
            tags=args.tags
        )
        print(f"\n✅ 已存储，ID: {memory_id}")

    elif args.cmd == "search":
        results = hub.retrieve(
            query=args.query,
            agents=args.agents,
            mem_types=args.types,
            limit=args.limit
        )
        print(f"\n🔍 找到 {len(results)} 条记忆:\n")
        for r in results:
            print(f"[{r['agent']}/{r['type']}] {r['title']}")
            print(f"  {r['content'][:100]}...")
            print()

    elif args.cmd == "list":
        if args.agent:
            results = hub.get_by_agent(args.agent, args.limit)
        elif args.type:
            results = hub.get_by_type(args.type, args.limit)
        else:
            results = hub.get_recent(args.limit)

        print(f"\n📋 共 {len(results)} 条记忆:\n")
        for r in results:
            print(f"[{r['agent']}/{r['type']}] {r['title']}")

    elif args.cmd == "stats":
        stats = hub.get_stats()
        print("\n📊 记忆统计:\n")
        print(f"总记忆数: {stats['total_memories']}")
        print("\n按Agent:")
        for agent, count in stats['by_agent'].items():
            print(f"  {agent}: {count}")
        print("\n按类型:")
        for mem_type, count in stats['by_type'].items():
            print(f"  {mem_type}: {count}")

    elif args.cmd == "recent":
        results = hub.get_recent(args.limit)
        print(f"\n🕐 最近的 {len(results)} 条记忆:\n")
        for r in results:
            print(f"[{r['created_at'][:19]}] [{r['agent']}/{r['type']}] {r['title']}")

    else:
        parser.print_help()