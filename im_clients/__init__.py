"""IM 客户端抽象接口(2026-07-03 从 dynamic_router/router.py 迁出)

定义 IM 模块的标准操作契约,供 FastLane/GhostLine 实现(ADR-002 F-4)。
原先这份代码被写进 dynamic_router/router.py,覆盖掉了 DynamicRouter;
现在独立成模块,router.py 恢复为语义路由器。

修复:原版各方法内引用 time.time() 但 time 只在 _request 局部 import,
所有 send/send_image/get_history/set_presence 一调用就 NameError。
现在统一模块级 import。

核心方法:
- send(text, target) → dict            发送文本消息
- send_image(image_bytes, target) → dict 发送图片消息
- get_history(limit) → List[dict]      获取聊天历史
- get_self_info() → dict               获取自身账号信息
- set_presence(status) → dict          设置在线状态
"""
from __future__ import annotations

import base64
import time
import uuid
from abc import ABC, abstractmethod
from typing import Dict, List


# ==================== IM 客户端抽象接口 ====================
class IMClient(ABC):
    """即时消息客户端抽象接口

    每个实现都必须处理:
    - 认证/授权(token/refresh)
    - 消息格式序列化(JSON/二进制)
    - 网络重试/降级
    """

    @abstractmethod
    def send(self, text: str, target: str = None) -> dict:
        """发送文本消息

        Returns:
            至少包含 status("ok"|"fail") / message_id / timestamp / target
        """

    @abstractmethod
    def send_image(self, image_bytes: bytes, target: str = None) -> dict:
        """发送图片消息(返回结构同 send)"""

    @abstractmethod
    def get_history(self, limit: int = 10) -> List[dict]:
        """获取聊天历史,每条含 id / content / timestamp / sender"""

    @abstractmethod
    def get_self_info(self) -> dict:
        """获取自身账号信息(id / nickname / avatar_url / status)"""

    @abstractmethod
    def set_presence(self, status: str) -> dict:
        """设置在线状态(online/offline/dnd),返回 status + timestamp"""


# ==================== IM 抽象注册表 ====================
IM_CLIENTS: Dict[str, IMClient] = {}


def register_im_client(name: str, client: IMClient) -> None:
    """注册 IM 客户端实现;name 已存在时抛 ValueError"""
    if name in IM_CLIENTS:
        raise ValueError(f"IM client '{name}' 已存在")
    IM_CLIENTS[name] = client


def get_im_client(name: str) -> IMClient:
    """按名称取 IM 客户端;不存在时抛 KeyError"""
    if name not in IM_CLIENTS:
        raise KeyError(f"IM client '{name}' 不存在")
    return IM_CLIENTS[name]


def list_im_clients() -> List[str]:
    return list(IM_CLIENTS.keys())


def clear_im_clients() -> None:
    """清除所有已注册的 IM 客户端(仅用于测试)"""
    IM_CLIENTS.clear()


def _resolve_name(im_name: str = None) -> str:
    if not im_name and "default" in IM_CLIENTS:
        im_name = "default"
    if not im_name:
        raise ValueError("未指定 IM 客户端名称且没有默认客户端")
    return im_name


def send_text_via_im(text: str, im_name: str = None, target: str = None) -> dict:
    """通过指定(或默认)IM 客户端发送文本消息"""
    return get_im_client(_resolve_name(im_name)).send(text, target)


def send_image_via_im(image_bytes: bytes, im_name: str = None, target: str = None) -> dict:
    """通过指定(或默认)IM 客户端发送图片消息"""
    return get_im_client(_resolve_name(im_name)).send_image(image_bytes, target)


def get_im_history(im_name: str = None, limit: int = 10) -> List[dict]:
    """获取指定(或默认)IM 客户端的历史消息"""
    return get_im_client(_resolve_name(im_name)).get_history(limit)


# ==================== 实现: SimpleX ====================
class SimpleXIMClient(IMClient):
    """SimpleX IM 客户端实现(https://simple-x.org)"""

    def __init__(self, api_endpoint: str, api_key: str, user_id: str = None):
        self.api_endpoint = api_endpoint.rstrip("/")
        self.api_key = api_key
        self.user_id = user_id

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        import requests

        url = f"{self.api_endpoint}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request(
                method=method, url=url, headers=headers, json=data, timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"status": "fail", "error": str(e)}

    def send(self, text: str, target: str = None) -> dict:
        target = target or self.user_id
        if not target:
            return {"status": "fail", "error": "未指定目标"}
        result = self._request("POST", "api/messages", {
            "content": text, "recipient_id": target, "type": "text",
        })
        return {
            "status": result.get("status") or "ok",
            "message_id": result.get("id"),
            "timestamp": result.get("timestamp", time.time()),
            "target": target,
        }

    def send_image(self, image_bytes: bytes, target: str = None) -> dict:
        target = target or self.user_id
        if not target:
            return {"status": "fail", "error": "未指定目标"}
        image_b64 = base64.b64encode(image_bytes).decode()
        result = self._request("POST", "api/messages", {
            "content": image_b64, "recipient_id": target, "type": "image",
        })
        return {
            "status": result.get("status") or "ok",
            "message_id": result.get("id", str(uuid.uuid4())),
            "timestamp": result.get("timestamp", time.time()),
            "target": target,
        }

    def get_history(self, limit: int = 10) -> List[dict]:
        result = self._request("GET", "api/messages", {
            "limit": limit, "include_media": "true",
        })
        if result.get("status") == "fail":
            return []
        messages = result.get("messages", [])
        for msg in messages:
            msg.setdefault("timestamp", time.time())
        return messages

    def get_self_info(self) -> dict:
        result = self._request("GET", "api/me")
        if result.get("status") == "fail":
            return {"id": self.user_id, "nickname": "SimpleX User",
                    "avatar_url": None, "status": "unknown"}
        user_info = result.get("user", {})
        return {
            "id": user_info.get("id", self.user_id),
            "nickname": user_info.get("display_name", "SimpleX User"),
            "avatar_url": user_info.get("avatar"),
            "status": user_info.get("status", "online"),
        }

    def set_presence(self, status: str) -> dict:
        result = self._request("POST", "api/status", {"status": status})
        return {"status": result.get("status") or "ok", "timestamp": time.time()}


def create_simplex_client(api_endpoint: str, api_key: str, user_id: str = None) -> SimpleXIMClient:
    """创建并注册 SimpleX IM 客户端(同时设为 default)"""
    client = SimpleXIMClient(api_endpoint, api_key, user_id)
    register_im_client("simplex", client)
    register_im_client("default", client)
    return client


# ==================== 实现: Matrix ====================
class MatrixIMClient(IMClient):
    """Matrix IM 客户端实现(分布式 IM 协议)"""

    def __init__(self, homeserver: str, access_token: str, user_id: str = None):
        self.homeserver = homeserver.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id

    def _request(self, method: str, path: str, data: dict = None) -> dict:
        import requests

        url = f"{self.homeserver}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.request(
                method=method, url=url, headers=headers, json=data, timeout=10.0
            )
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            return {"error": str(e)}

    def send(self, text: str, target: str = None) -> dict:
        target = target or self.user_id
        if not target:
            return {"status": "fail", "error": "未指定目标"}
        # Matrix ID 格式: @localpart:domain
        if not target.startswith("@") or ":" not in target:
            return {"status": "fail", "error": f"无效的 Matrix 目标: {target}"}
        result = self._request("PUT", f"_matrix/client/r0/rooms/{target}/send/m.text", {
            "body": {"msg": text, "format": "org.matrix.custom.html"},
        })
        if "error" in result:
            return {"status": "fail", "error": result["error"]}
        return {
            "status": "ok",
            "message_id": result.get("event_id"),
            "timestamp": time.time(),
            "target": target,
        }

    def send_image(self, image_bytes: bytes, target: str = None) -> dict:
        target = target or self.user_id
        if not target:
            return {"status": "fail", "error": "未指定目标"}
        image_b64 = base64.b64encode(image_bytes).decode()
        upload_result = self._request("POST", "_matrix/media/r0/upload", {
            "file": image_b64, "filename": f"image_{int(time.time())}.png",
        })
        if "error" in upload_result:
            return {"status": "fail", "error": upload_result["error"]}
        result = self._request("PUT", f"_matrix/client/r0/rooms/{target}/send/m.image", {
            "body": {"url": upload_result.get("content_uri"), "info": {"w": 100, "h": 100}},
        })
        if "error" in result:
            return {"status": "fail", "error": result["error"]}
        return {
            "status": "ok",
            "message_id": result.get("event_id"),
            "timestamp": time.time(),
            "target": target,
        }

    def get_history(self, limit: int = 10) -> List[dict]:
        result = self._request("GET", f"_matrix/client/r0/rooms/{self.user_id}/messages", {
            "limit": limit, "dir": "b",
        })
        if "error" in result:
            return []
        formatted = []
        for msg in result.get("chunk", []):
            item = {
                "id": msg.get("event_id"),
                "content": msg.get("body", ""),
                "timestamp": msg.get("origin_server_ts", time.time()),
                "sender": msg.get("sender"),
                "type": msg.get("type", "unknown"),
            }
            if msg.get("type") == "m.image":
                item["content"] = msg.get("content", {}).get("url", "")
            formatted.append(item)
        return formatted

    def get_self_info(self) -> dict:
        result = self._request("GET", "_matrix/client/r0/account/whoami")
        if "error" in result:
            return {"id": self.user_id, "nickname": "Matrix User",
                    "avatar_url": None, "status": "unknown"}
        user_info = result.get("user_id", self.user_id)
        return {
            "id": user_info,
            "nickname": result.get("displayname", user_info),
            "avatar_url": None,
            "status": "online",
        }

    def set_presence(self, status: str) -> dict:
        result = self._request("PUT", "_matrix/client/r0/presence/me", {"presence": status})
        if "error" in result:
            return {"status": "fail", "error": result["error"]}
        return {"status": "ok", "timestamp": time.time()}


def create_matrix_client(homeserver: str, access_token: str, user_id: str = None) -> MatrixIMClient:
    """创建并注册 Matrix IM 客户端(同时设为 default)"""
    client = MatrixIMClient(homeserver, access_token, user_id)
    register_im_client("matrix", client)
    register_im_client("default", client)
    return client
