# -*- coding: utf-8 -*-
"""
Peekaboo-W MCP Unified Management
Phase 4-8 Core Module - MCP (Model Context Protocol) Support

MCP Architecture:
    ┌────────────────────────────────────────────────┐
    │              MCP Manager                       │
    │  (Unified Protocol Handler & Registry)         │
    └────────────────────┬─────────────────────────┘
                         │
    ┌─────────────────────┼─────────────────────────┐
    │                     │                         │
    ▼                     ▼                         ▼
┌──────────┐        ┌──────────┐           ┌──────────┐
│   MCP    │        │   MCP    │           │   MCP    │
│  Server  │        │  Client  │           │  Tools   │
│  (本地)  │        │ (远程)   │           │ (定义)   │
└──────────┘        └──────────┘           └──────────┘

Features:
1. MCP Server Registry
2. Tool Definition & Discovery
3. Protocol Bridge
4. Resource Management
5. Cross-Protocol Adaptation
"""

import sys
import json
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass, field
import hashlib

sys.path.insert(0, str(Path(__file__).parent.parent))


class MCPProtocol(Enum):
    """Supported MCP protocols"""
    STDIO = "stdio"
    HTTP = "http"
    WEBSOCKET = "websocket"
    SSE = "sse"


class MCPResourceType(Enum):
    """MCP resource types"""
    FILE = "file"
    DATABASE = "database"
    API = "api"
    COMPUTE = "compute"
    NETWORK = "network"


@dataclass
class MCPTool:
    """MCP Tool definition"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    category: str = "general"
    tags: List[str] = field(default_factory=list)
    enabled: bool = True
    version: str = "1.0.0"
    handler: Optional[Callable] = None


@dataclass
class MCPResource:
    """MCP Resource definition"""
    uri: str
    name: str
    resource_type: MCPResourceType
    description: str
    mime_type: str = "application/json"
    size: Optional[int] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPServer:
    """MCP Server configuration"""
    name: str
    command: str
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    protocol: MCPProtocol = MCPProtocol.STDIO
    url: Optional[str] = None
    status: str = "stopped"
    tools: List[MCPTool] = field(default_factory=list)
    resources: List[MCPResource] = field(default_factory=list)
    last_heartbeat: str = ""


class MCPToolRegistry:
    """Registry for MCP tools"""

    def __init__(self):
        self.tools: Dict[str, MCPTool] = {}
        self.categories: Dict[str, List[str]] = {}
        self._initialize_default_tools()

    def _initialize_default_tools(self):
        """Initialize default built-in tools"""
        default_tools = [
            MCPTool(
                name="web_search",
                description="Search the web for information",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 10}
                    },
                    "required": ["query"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "results": {"type": "array"},
                        "count": {"type": "integer"}
                    }
                },
                category="search",
                tags=["web", "search", "information"]
            ),
            MCPTool(
                name="file_read",
                description="Read content from a file",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "encoding": {"type": "string", "default": "utf-8"}
                    },
                    "required": ["path"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "lines": {"type": "integer"}
                    }
                },
                category="file",
                tags=["file", "read", "io"]
            ),
            MCPTool(
                name="file_write",
                description="Write content to a file",
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                        "append": {"type": "boolean", "default": False}
                    },
                    "required": ["path", "content"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "bytes_written": {"type": "integer"}
                    }
                },
                category="file",
                tags=["file", "write", "io"]
            ),
            MCPTool(
                name="browser_automation",
                description="Automate browser actions",
                input_schema={
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["open", "click", "type", "scroll", "screenshot"]},
                        "params": {"type": "object"}
                    },
                    "required": ["action"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean"},
                        "result": {"type": "any"}
                    }
                },
                category="automation",
                tags=["browser", "automation", "pyautogui"]
            ),
            MCPTool(
                name="memory_store",
                description="Store information in agent memory",
                input_schema={
                    "type": "object",
                    "properties": {
                        "agent": {"type": "string"},
                        "title": {"type": "string"},
                        "content": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": ["agent", "title", "content"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "memory_id": {"type": "string"},
                        "success": {"type": "boolean"}
                    }
                },
                category="memory",
                tags=["memory", "storage", "persistence"]
            ),
            MCPTool(
                name="memory_search",
                description="Search agent memory",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "agent": {"type": "string"},
                        "limit": {"type": "integer", "default": 10}
                    },
                    "required": ["query"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "results": {"type": "array"},
                        "count": {"type": "integer"}
                    }
                },
                category="memory",
                tags=["memory", "search", "retrieval"]
            ),
            MCPTool(
                name="code_execute",
                description="Execute code snippets",
                input_schema={
                    "type": "object",
                    "properties": {
                        "language": {"type": "string"},
                        "code": {"type": "string"},
                        "timeout": {"type": "integer", "default": 30}
                    },
                    "required": ["code"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "output": {"type": "string"},
                        "error": {"type": "string"},
                        "exit_code": {"type": "integer"}
                    }
                },
                category="execution",
                tags=["code", "execution", "sandbox"]
            ),
            MCPTool(
                name="schedule_task",
                description="Schedule a task for later execution",
                input_schema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string"},
                        "schedule": {"type": "string"},
                        "repeat": {"type": "boolean", "default": False}
                    },
                    "required": ["task", "schedule"]
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "task_id": {"type": "string"},
                        "scheduled_at": {"type": "string"}
                    }
                },
                category="scheduler",
                tags=["schedule", "cron", "automation"]
            )
        ]

        for tool in default_tools:
            self.register(tool)

    def register(self, tool: MCPTool):
        """Register a new tool"""
        self.tools[tool.name] = tool
        if tool.category not in self.categories:
            self.categories[tool.category] = []
        if tool.name not in self.categories[tool.category]:
            self.categories[tool.category].append(tool.name)
        print(f"[MCP] Registered tool: {tool.name} ({tool.category})")

    def unregister(self, tool_name: str):
        """Unregister a tool"""
        if tool_name in self.tools:
            tool = self.tools[tool_name]
            if tool.category in self.categories:
                self.categories[tool.category].remove(tool_name)
            del self.tools[tool_name]
            print(f"[MCP] Unregistered tool: {tool_name}")

    def get_tool(self, tool_name: str) -> Optional[MCPTool]:
        """Get tool by name"""
        return self.tools.get(tool_name)

    def list_tools(self, category: str = None) -> List[MCPTool]:
        """List all tools, optionally filtered by category"""
        if category:
            tool_names = self.categories.get(category, [])
            return [self.tools[name] for name in tool_names if name in self.tools]
        return list(self.tools.values())

    def search_tools(self, query: str) -> List[MCPTool]:
        """Search tools by query"""
        results = []
        query_lower = query.lower()

        for tool in self.tools.values():
            if query_lower in tool.name.lower():
                results.append(tool)
            elif query_lower in tool.description.lower():
                results.append(tool)
            elif any(query_lower in tag.lower() for tag in tool.tags):
                results.append(tool)

        return results


class MCPServerManager:
    """Manages MCP server connections"""

    def __init__(self):
        self.servers: Dict[str, MCPServer] = {}
        self._initialize_default_servers()

    def _initialize_default_servers(self):
        """Initialize default MCP servers"""
        # File System MCP Server
        self.servers["filesystem"] = MCPServer(
            name="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "."],
            protocol=MCPProtocol.STDIO,
            tools=[
                MCPTool(
                    name="fs_read_directory",
                    description="Read directory contents",
                    input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                    output_schema={"type": "object"}
                )
            ]
        )

        # Memory MCP Server (for agent memory)
        self.servers["memory"] = MCPServer(
            name="memory",
            command="python",
            args=["-m", "src.mcp_memory_server"],
            protocol=MCPProtocol.STDIO
        )

        print(f"[MCP] Initialized {len(self.servers)} default servers")

    def add_server(self, server: MCPServer):
        """Add an MCP server"""
        self.servers[server.name] = server
        print(f"[MCP] Added server: {server.name}")

    def remove_server(self, server_name: str):
        """Remove an MCP server"""
        if server_name in self.servers:
            del self.servers[server_name]
            print(f"[MCP] Removed server: {server_name}")

    def start_server(self, server_name: str) -> bool:
        """Start an MCP server"""
        if server_name not in self.servers:
            print(f"[MCP] Server not found: {server_name}")
            return False

        server = self.servers[server_name]
        # In real implementation, this would spawn the process
        server.status = "running"
        server.last_heartbeat = datetime.now().isoformat()
        print(f"[MCP] Started server: {server_name}")
        return True

    def stop_server(self, server_name: str) -> bool:
        """Stop an MCP server"""
        if server_name not in self.servers:
            return False

        server = self.servers[server_name]
        server.status = "stopped"
        print(f"[MCP] Stopped server: {server_name}")
        return True

    def list_servers(self) -> List[MCPServer]:
        """List all servers"""
        return list(self.servers.values())


class MCPManager:
    """
    MCP Manager - Unified management of all MCP resources
    Central entry point for MCP operations
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.tool_registry = MCPToolRegistry()
            cls._instance.server_manager = MCPServerManager()
            cls._instance.protocol_adapters: Dict[str, Any] = {}
            cls._instance.execution_history: List[Dict] = []
            cls._instance.config = {
                "auto_discover": True,
                "max_concurrent_tools": 10,
                "tool_timeout": 60,
                "enable_caching": True
            }
            print("[MCP] MCP Manager initialized")
            print(f"[MCP] Available tools: {len(cls._instance.tool_registry.tools)}")
            print(f"[MCP] Available servers: {len(cls._instance.server_manager.servers)}")
        return cls._instance

    def execute_tool(self, tool_name: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Execute an MCP tool"""
        tool = self.tool_registry.get_tool(tool_name)

        if not tool:
            return {"error": f"Tool not found: {tool_name}"}

        if not tool.enabled:
            return {"error": f"Tool disabled: {tool_name}"}

        # If tool has a handler, use it
        if tool.handler:
            result = tool.handler(params)
        else:
            # Simulate execution for built-in tools
            result = self._execute_builtin_tool(tool_name, params)

        # Record execution
        self.execution_history.append({
            "tool": tool_name,
            "params": params,
            "result": result,
            "timestamp": datetime.now().isoformat()
        })

        return result

    def _execute_builtin_tool(self, tool_name: str, params: Dict) -> Dict:
        """Execute built-in tool logic"""
        results = {
            "success": True,
            "tool": tool_name,
            "params": params
        }

        if tool_name == "web_search":
            results["results"] = []
            results["count"] = 0

        elif tool_name == "file_read":
            try:
                path = params.get("path", "")
                with open(path, 'r', encoding=params.get("encoding", "utf-8")) as f:
                    content = f.read()
                results["content"] = content
                results["lines"] = len(content.split('\n'))
            except Exception as e:
                results["error"] = str(e)
                results["success"] = False

        elif tool_name == "file_write":
            try:
                path = params.get("path", "")
                content = params.get("content", "")
                mode = "a" if params.get("append", False) else "w"
                with open(path, mode) as f:
                    bytes_written = f.write(content)
                results["bytes_written"] = bytes_written
            except Exception as e:
                results["error"] = str(e)
                results["success"] = False

        elif tool_name == "memory_store":
            # Delegate to SharedMemory
            try:
                from src.shared_memory import SharedMemory
                sm = SharedMemory()
                memory_id = sm.store(
                    agent=params.get("agent", "unknown"),
                    title=params.get("title", ""),
                    content=params.get("content", ""),
                    mem_type="mcp",
                    tags=params.get("tags", [])
                )
                results["memory_id"] = memory_id
            except Exception as e:
                results["error"] = str(e)
                results["success"] = False

        elif tool_name == "memory_search":
            try:
                from src.shared_memory import SharedMemory
                sm = SharedMemory()
                results_data = sm.search(
                    query=params.get("query", ""),
                    agent=params.get("agent"),
                    limit=params.get("limit", 10)
                )
                results["results"] = results_data
                results["count"] = len(results_data)
            except Exception as e:
                results["error"] = str(e)
                results["success"] = False

        elif tool_name == "browser_automation":
            results["result"] = "Browser automation executed (simulated)"

        elif tool_name == "code_execute":
            results["output"] = "Code execution (simulated)"
            results["exit_code"] = 0

        elif tool_name == "schedule_task":
            task_id = hashlib.md5(f"{params.get('task', '')}{datetime.now().isoformat()}".encode()).hexdigest()[:8]
            results["task_id"] = task_id
            results["scheduled_at"] = params.get("schedule", "")

        return results

    def get_capabilities(self) -> Dict[str, Any]:
        """Get MCP capabilities summary"""
        return {
            "tools": {
                "total": len(self.tool_registry.tools),
                "categories": list(self.tool_registry.categories.keys()),
                "tools_by_category": {
                    cat: len(names) for cat, names in self.tool_registry.categories.items()
                }
            },
            "servers": {
                "total": len(self.server_manager.servers),
                "running": sum(1 for s in self.server_manager.servers.values() if s.status == "running")
            },
            "execution_history": {
                "total": len(self.execution_history),
                "recent": self.execution_history[-5:] if self.execution_history else []
            }
        }

    def generate_tool_schema(self) -> Dict:
        """Generate JSON schema for all tools (for LLM consumption)"""
        tools_list = []

        for tool in self.tool_registry.list_tools():
            tools_list.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "output_schema": tool.output_schema,
                "category": tool.category,
                "tags": tool.tags
            })

        return {
            "mcp_version": "1.0.0",
            "tools": tools_list
        }


# Singleton accessor
def get_mcp_manager() -> MCPManager:
    """Get or create MCP Manager singleton"""
    return MCPManager()


def list_available_tools() -> List[str]:
    """List all available tool names"""
    manager = get_mcp_manager()
    return list(manager.tool_registry.tools.keys())


# CLI Interface
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Peekaboo-W MCP Manager")
    sub = parser.add_subparsers(dest="cmd")

    # List tools
    sub.add_parser("tools", help="List all available tools")

    # Tool info
    tool_cmd = sub.add_parser("tool", help="Show tool details")
    tool_cmd.add_argument("name", help="Tool name")

    # Search tools
    search_cmd = sub.add_parser("search", help="Search tools")
    search_cmd.add_argument("query", help="Search query")

    # Execute tool
    exec_cmd = sub.add_parser("execute", help="Execute a tool")
    exec_cmd.add_argument("name", help="Tool name")
    exec_cmd.add_argument("params", help="JSON params")

    # List servers
    sub.add_parser("servers", help="List MCP servers")

    # Start server
    start_cmd = sub.add_parser("start", help="Start an MCP server")
    start_cmd.add_argument("name", help="Server name")

    # Stop server
    stop_cmd = sub.add_parser("stop", help="Stop an MCP server")
    stop_cmd.add_argument("name", help="Server name")

    # Capabilities
    sub.add_parser("capabilities", help="Show MCP capabilities")

    # Schema
    sub.add_parser("schema", help="Generate tool schema JSON")

    args = parser.parse_args()

    manager = get_mcp_manager()

    if args.cmd == "tools":
        tools = manager.tool_registry.list_tools()
        print(f""
[MCP] Available Tools ({len(tools)})")
        for category, names in manager.tool_registry.categories.items():
            print(f""
  [{category}]")
            for name in names:
                tool = manager.tool_registry.get_tool(name)
                print(f"    - {name}: {tool.description}")

    elif args.cmd == "tool":
        tool = manager.tool_registry.get_tool(args.name)
        if tool:
            print(f""
[TOOL] {tool.name}")
            print(f"Description: {tool.description}")
            print(f"Category: {tool.category}")
            print(f"Tags: {', '.join(tool.tags)}")
            print(f"Input Schema: {json.dumps(tool.input_schema, indent=2)}")
        else:
            print(f"[ERROR] Tool not found: {args.name}")

    elif args.cmd == "search":
        results = manager.tool_registry.search_tools(args.query)
        print(f""
[SEARCH] Results for '{args.query}': {len(results)} tools")
        for tool in results:
            print(f"  - {tool.name} ({tool.category})")

    elif args.cmd == "execute":
        try:
            params = json.loads(args.params)
            result = manager.execute_tool(args.name, params)
            print(f""
[RESULT] {json.dumps(result, indent=2, ensure_ascii=False)}")
        except json.JSONDecodeError as e:
            print(f"[ERROR] Invalid JSON params: {e}")

    elif args.cmd == "servers":
        servers = manager.server_manager.list_servers()
        print(f""
[MCP] Servers ({len(servers)})")
        for server in servers:
            print(f"  [{server.status}] {server.name}")

    elif args.cmd == "start":
        success = manager.server_manager.start_server(args.name)
        print(f""
[{'OK' if success else 'ERROR'}] Server {args.name} started")

    elif args.cmd == "stop":
        success = manager.server_manager.stop_server(args.name)
        print(f""
[{'OK' if success else 'ERROR'}] Server {args.name} stopped")

    elif args.cmd == "capabilities":
        caps = manager.get_capabilities()
        print(f""
[MCP] Capabilities")
        print(f"Tools: {caps['tools']['total']} in {len(caps['tools']['categories'])} categories")
        print(f"Servers: {caps['servers']['total']} total, {caps['servers']['running']} running")
        print(f"Executions: {caps['execution_history']['total']} total")

    elif args.cmd == "schema":
        schema = manager.generate_tool_schema()
        print(json.dumps(schema, indent=2, ensure_ascii=False))

    else:
        parser.print_help()