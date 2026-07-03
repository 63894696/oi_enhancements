# -*- coding: utf-8 -*-
"""
Peekaboo-W Multi-Agent Mode Implementation
Phase 4-8 Core Module - Multi-Agent System

Multi-Agent Mode Architecture:
    ┌─────────────────────────────────────────────┐
    │              MultiAgentHub                  │
    │         (Multi-Agent Central Controller)     │
    └────────────────────┬────────────────────────┘
                        │
    ┌───────────────────┼───────────────────┐
    │                   │                   │
    ▼                   ▼                   ▼
┌──────────┐      ┌──────────┐      ┌──────────┐
│   Agent │      │   Agent  │      │   Agent  │
│  Pecky  │      │   News   │      │   Code   │
│ (本地)  │      │ (记者)   │      │ (开发者) │
└──────────┘      └──────────┘      └──────────┘

Capabilities:
1. Agent Registry & Discovery
2. Task Distribution & Routing
3. Cross-Agent Memory Sharing
4. Parallel Execution
5. Result Aggregation
"""

import sys
import json
import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent))

# Import shared memory for cross-agent communication
try:
    from src.shared_memory import SharedMemory, AgentMemory
    SHARED_MEMORY_AVAILABLE = True
except ImportError:
    SHARED_MEMORY_AVAILABLE = False
    print("[WARNING] Shared memory not available, using local fallback")


class AgentStatus(Enum):
    """Agent status enumeration"""
    IDLE = "idle"
    BUSY = "busy"
    ERROR = "error"
    OFFLINE = "offline"


class TaskPriority(Enum):
    """Task priority levels"""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    URGENT = 4


@dataclass
class AgentInfo:
    """Agent information structure"""
    name: str
    role: str
    description: str
    capabilities: List[str]
    status: AgentStatus = AgentStatus.IDLE
    last_active: str = ""
    task_count: int = 0
    memory_count: int = 0
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Task:
    """Task structure for agent distribution"""
    id: str
    description: str
    required_capabilities: List[str]
    priority: TaskPriority = TaskPriority.NORMAL
    target_agent: Optional[str] = None
    status: str = "pending"
    result: Any = None
    created_at: str = ""
    completed_at: str = ""


class AgentRegistry:
    """
    Agent Registry - Central registry for all agents
    Implements agent discovery and capability matching
    """

    def __init__(self):
        self.agents: Dict[str, AgentInfo] = {}
        self._initialize_default_agents()

    def _initialize_default_agents(self):
        """Initialize default built-in agents"""
        default_agents = [
            AgentInfo(
                name="pecky",
                role="archivist",
                description="本地文件存档专家，擅长浏览器自动化和文章收藏",
                capabilities=["browser_automation", "file_archive", "obsidian_sync", "web_scraping"],
                config={"auto_archive": True, "target_folder": "archive"}
            ),
            AgentInfo(
                name="news",
                role="researcher",
                description="深度调研专家，擅长新闻分析、多源对比、归因分析",
                capabilities=["web_search", "fact_check", "multi_source_compare", "causal_analysis"],
                config={"depth": "deep", "sources_limit": 5}
            ),
            AgentInfo(
                name="code",
                role="developer",
                description="代码开发专家，擅长代码生成、调试、重构",
                capabilities=["code_generation", "debugging", "refactoring", "code_review"],
                config={"language": "python", "auto_test": True}
            ),
            AgentInfo(
                name="creative",
                role="creative",
                description="创意专家，擅长头脑风暴、内容创作、设计",
                capabilities=["brainstorming", "content_creation", "design_ideas", "storytelling"],
                config={"creative_mode": "balanced"}
            ),
            AgentInfo(
                name="legal",
                role="legal_expert",
                description="法律分析专家，擅长合同审查、法规解读",
                capabilities=["contract_review", "regulation_analysis", "compliance_check"],
                config={"jurisdiction": "cn"}
            ),
            AgentInfo(
                name="finance",
                role="financial_analyst",
                description="财经分析专家，擅长财报分析、投资评估",
                capabilities=["financial_analysis", "investment_evaluation", "market_research"],
                config={"market": "cn"}
            ),
            AgentInfo(
                name="tech",
                role="tech_researcher",
                description="技术研究专家，擅长技术调研、竞品分析",
                capabilities=["tech_research", "competitor_analysis", "trend_forecasting"],
                config={"tech_focus": "ai"}
            )
        ]

        for agent in default_agents:
            self.register(agent)

    def register(self, agent_info: AgentInfo):
        """Register a new agent"""
        self.agents[agent_info.name] = agent_info
        print(f"[REGISTRY] Registered agent: {agent_info.name} ({agent_info.role})")

    def unregister(self, agent_name: str):
        """Unregister an agent"""
        if agent_name in self.agents:
            del self.agents[agent_name]
            print(f"[REGISTRY] Unregistered agent: {agent_name}")

    def get_agent(self, agent_name: str) -> Optional[AgentInfo]:
        """Get agent information by name"""
        return self.agents.get(agent_name)

    def find_agent_by_capability(self, capability: str) -> List[AgentInfo]:
        """Find agents that have a specific capability"""
        return [
            agent for agent in self.agents.values()
            if capability in agent.capabilities
        ]

    def list_agents(self) -> List[AgentInfo]:
        """List all registered agents"""
        return list(self.agents.values())

    def update_status(self, agent_name: str, status: AgentStatus):
        """Update agent status"""
        if agent_name in self.agents:
            self.agents[agent_name].status = status
            self.agents[agent_name].last_active = datetime.now().isoformat()

    def increment_task_count(self, agent_name: str):
        """Increment task count for an agent"""
        if agent_name in self.agents:
            self.agents[agent_name].task_count += 1


class TaskRouter:
    """
    Task Router - Routes tasks to appropriate agents
    Implements task distribution logic
    """

    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self.tasks: Dict[str, Task] = {}

    def create_task(self, description: str, capabilities: List[str],
                   priority: TaskPriority = TaskPriority.NORMAL,
                   target_agent: Optional[str] = None) -> Task:
        """Create a new task"""
        task_id = f"task_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.tasks)}"
        task = Task(
            id=task_id,
            description=description,
            required_capabilities=capabilities,
            priority=priority,
            target_agent=target_agent,
            created_at=datetime.now().isoformat()
        )
        self.tasks[task_id] = task
        return task

    def route_task(self, task: Task) -> Optional[str]:
        """Route task to appropriate agent"""
        # If target agent specified, use it
        if task.target_agent and task.target_agent in self.registry.agents:
            return task.target_agent

        # Find agents with required capabilities
        for capability in task.required_capabilities:
            agents = self.registry.find_agent_by_capability(capability)
            if agents:
                # Select first available agent
                for agent in agents:
                    if agent.status == AgentStatus.IDLE:
                        return agent.name

        return None

    def execute_task(self, task: Task, execute_fn: Callable) -> Any:
        """Execute task with given function"""
        target = self.route_task(task)
        if not target:
            return {"error": "No suitable agent found", "task_id": task.id}

        # Update agent status
        self.registry.update_status(target, AgentStatus.BUSY)
        self.registry.increment_task_count(target)

        try:
            # Execute task
            result = execute_fn(target, task)

            # Mark complete
            task.status = "completed"
            task.completed_at = datetime.now().isoformat()
            task.result = result

            return result

        except Exception as e:
            task.status = "failed"
            task.result = {"error": str(e)}
            return {"error": str(e), "task_id": task.id}

        finally:
            self.registry.update_status(target, AgentStatus.IDLE)


class MultiAgentHub:
    """
    Multi-Agent Hub - Central controller for multi-agent operations
    Coordinates all agents and manages cross-agent communication
    """

    def __init__(self):
        self.registry = AgentRegistry()
        self.router = TaskRouter(self.registry)

        # Shared memory for cross-agent communication
        self.shared_memory = SharedMemory() if SHARED_MEMORY_AVAILABLE else None

        # Agent execution history
        self.execution_history: List[Dict] = []

        # Configuration
        self.config = {
            "enable_parallel": True,
            "max_concurrent_tasks": 5,
            "task_timeout": 300,  # seconds
            "enable_memory_sharing": True
        }

        print("[HUB] Multi-Agent Hub initialized")
        print(f"[HUB] Registered agents: {len(self.registry.agents)}")

    def dispatch_task(self, description: str, capabilities: List[str],
                     priority: int = 2, target_agent: str = None) -> Dict:
        """Dispatch a task to appropriate agent"""
        task = self.router.create_task(
            description=description,
            capabilities=capabilities,
            priority=TaskPriority(priority),
            target_agent=target_agent
        )

        def execute(agent_name: str, task: Task) -> Dict:
            """Execute function for task"""
            print(f"[HUB] Task {task.id} dispatched to {agent_name}")

            # Simulate execution (in real implementation, call actual agent)
            result = {
                "task_id": task.id,
                "agent": agent_name,
                "status": "completed",
                "result": f"Task executed by {agent_name}",
                "timestamp": datetime.now().isoformat()
            }

            # Store in shared memory
            if self.shared_memory and self.config.get("enable_memory_sharing"):
                self.shared_memory.store(
                    agent=agent_name,
                    title=f"Task: {task.description[:50]}",
                    content=f"Task {task.id}: {task.description}\nResult: {result['result']}",
                    mem_type="task",
                    tags=capabilities
                )

            return result

        result = self.router.execute_task(task, execute)

        # Record in history
        self.execution_history.append({
            "task": task,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })

        return result

    def dispatch_parallel(self, tasks: List[Dict]) -> List[Dict]:
        """Execute multiple tasks in parallel"""
        results = []

        if not self.config.get("enable_parallel"):
            # Sequential execution
            for task in tasks:
                result = self.dispatch_task(
                    description=task.get("description", ""),
                    capabilities=task.get("capabilities", []),
                    priority=task.get("priority", 2),
                    target_agent=task.get("target_agent")
                )
                results.append(result)
        else:
            # Parallel execution (simulated with threads)
            import concurrent.futures

            def execute_single(task):
                return self.dispatch_task(
                    description=task.get("description", ""),
                    capabilities=task.get("capabilities", []),
                    priority=task.get("priority", 2),
                    target_agent=task.get("target_agent")
                )

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.config.get("max_concurrent_tasks", 5)) as executor:
                futures = [executor.submit(execute_single, task) for task in tasks]
                results = [f.result() for f in futures]

        return results

    def get_agent_status(self) -> Dict:
        """Get status of all agents"""
        return {
            "total_agents": len(self.registry.agents),
            "agents": [
                {
                    "name": agent.name,
                    "role": agent.role,
                    "status": agent.status.value,
                    "capabilities": agent.capabilities,
                    "task_count": agent.task_count,
                    "last_active": agent.last_active
                }
                for agent in self.registry.list_agents()
            ]
        }

    def get_statistics(self) -> Dict:
        """Get system statistics"""
        return {
            "total_tasks": len(self.execution_history),
            "completed_tasks": sum(1 for h in self.execution_history if h["result"].get("status") == "completed"),
            "failed_tasks": sum(1 for h in self.execution_history if "error" in h["result"]),
            "agent_distribution": {
                agent.name: agent.task_count
                for agent in self.registry.list_agents()
            }
        }

    def search_agents(self, query: str) -> List[Dict]:
        """Search agents by query (capability or role)"""
        results = []

        for agent in self.registry.list_agents():
            # Search in capabilities
            if any(query.lower() in cap.lower() for cap in agent.capabilities):
                results.append({
                    "name": agent.name,
                    "role": agent.role,
                    "match": "capability",
                    "matched_items": [cap for cap in agent.capabilities if query.lower() in cap.lower()]
                })
            # Search in role
            elif query.lower() in agent.role.lower():
                results.append({
                    "name": agent.name,
                    "role": agent.role,
                    "match": "role",
                    "matched_items": [agent.role]
                })

        return results


# Convenience functions
def create_hub() -> MultiAgentHub:
    """Create and return Multi-Agent Hub instance"""
    return MultiAgentHub()


def get_hub() -> MultiAgentHub:
    """Get or create singleton Multi-Agent Hub"""
    if not hasattr(get_hub, "_instance"):
        get_hub._instance = create_hub()
    return get_hub._instance


# CLI Interface
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Multi-Agent Hub - Task Dispatch System")
    sub = parser.add_subparsers(dest="cmd")

    # Status command
    sub.add_parser("status", help="Show agent status")

    # Stats command
    sub.add_parser("stats", help="Show statistics")

    # List agents
    sub.add_parser("list", help="List all agents")

    # Dispatch task
    dispatch_cmd = sub.add_parser("dispatch", help="Dispatch a task")
    dispatch_cmd.add_argument("--desc", "-d", required=True, help="Task description")
    dispatch_cmd.add_argument("--cap", "-c", nargs="+", required=True, help="Required capabilities")
    dispatch_cmd.add_argument("--agent", "-a", help="Target agent (optional)")
    dispatch_cmd.add_argument("--priority", "-p", type=int, default=2, help="Priority (1-4)")

    # Search agents
    search_cmd = sub.add_parser("search", help="Search agents")
    search_cmd.add_argument("query", help="Search query")

    args = parser.parse_args()

    hub = get_hub()

    if args.cmd == "status":
        status = hub.get_agent_status()
        print("\n[STATUS] Agent Status")
        print(f"Total Agents: {status['total_agents']}\n")
        for agent in status['agents']:
            print(f"  [{agent['status']}] {agent['name']} ({agent['role']})")
            print(f"    Capabilities: {', '.join(agent['capabilities'])}")
            print(f"    Tasks: {agent['task_count']}, Last Active: {agent['last_active'][:19] if agent['last_active'] else 'Never'}")
            print()

    elif args.cmd == "stats":
        stats = hub.get_statistics()
        print("\n[STATS] System Statistics")
        print(f"Total Tasks: {stats['total_tasks']}")
        print(f"Completed: {stats['completed_tasks']}")
        print(f"Failed: {stats['failed_tasks']}")
        print("\nAgent Distribution:")
        for agent, count in stats['agent_distribution'].items():
            print(f"  {agent}: {count} tasks")

    elif args.cmd == "list":
        agents = hub.registry.list_agents()
        print("\n[LIST] Registered Agents")
        print(f"Total: {len(agents)}\n")
        for agent in agents:
            print(f"  {agent.name} - {agent.role}")
            print(f"    {agent.description}")
            print()

    elif args.cmd == "dispatch":
        result = hub.dispatch_task(
            description=args.desc,
            capabilities=args.cap,
            priority=args.priority,
            target_agent=args.agent
        )
        print(f"\n[RESULT] {result}")

    elif args.cmd == "search":
        results = hub.search_agents(args.query)
        print(f"\n[SEARCH] Results for '{args.query}'")
        print(f"Found: {len(results)} agents\n")
        for r in results:
            print(f"  {r['name']} ({r['match']})")
            print(f"    Matched: {', '.join(r['matched_items'])}")

    else:
        parser.print_help()