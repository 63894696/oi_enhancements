"""
prisir_work/video_checkpoint.py — Workflow checkpoint 协议(Phase 9 OM-P1, 2026-09-28)。

承接 [[prisIr-openmontage-recon]] 用户拍板:
  「选 C(学习借鉴 OpenMontage)+ 免费资源,P1 checkpoint 协议 ship」

## 定位
让 video workflow 在 step 失败 / 系统崩溃 / 中断后,**从最近成功的 checkpoint 恢复**,
不需要从 step 0 重头跑。

借鉴 OpenMontage 的 checkpoint 设计模式:
- 每个 stage 落 checkpoint JSON 到本地磁盘
- 失败时返「最近成功 step」+ 「pending steps」
- 重启调 `resume_from(workflow_id)`,自动跳过已完成的 step
- LRU 清理,只保留最近 N 个 workflow(默认 20)

## 不存什么
- ❌ 二进制文件本身(视频/图片/音频)— 只存路径 + metadata
- ❌ LLM 完整响应文本 — 只存摘要 + 关键 ID

## 存什么(JSON)
- workflow_id(每次 run 唯一)
- step_id
- timestamp(ISO)
- status: ok / failed
- payload: 简化后的 step 产出(只保留 path + metadata)
- error: 失败时存
- attempt: 第几次尝试(失败重试计数)

## 关键 API
  - CheckpointManager(root=".prisIrai/checkpoints")
    - save(workflow_id, step_id, payload) → None
    - load_latest(workflow_id) → Optional[Checkpoint]
    - load_step(workflow_id, step_id) → Optional[Checkpoint]
    - resume_from(workflow_id, steps_done_ids) → List[str](已完成的 step id 列表)
    - cleanup_lru(keep=20) → None
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

__all__ = ["CheckpointManager", "Checkpoint", "DEFAULT_CHECKPOINT_ROOT"]

log = logging.getLogger("prisir_work.video_checkpoint")

DEFAULT_CHECKPOINT_ROOT = ".prisIrai/checkpoints"


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    """单个 step 的 checkpoint。"""
    workflow_id: str
    step_id: str
    timestamp: float
    status: str  # "ok" / "failed"
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    attempt: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Checkpoint":
        return cls(
            workflow_id=d["workflow_id"],
            step_id=d["step_id"],
            timestamp=d.get("timestamp", time.time()),
            status=d.get("status", "ok"),
            payload=d.get("payload") or {},
            error=d.get("error", ""),
            attempt=d.get("attempt", 1),
        )


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class CheckpointManager:
    """Checkpoint 落盘 + 恢复管理器。

    用法:
        cm = CheckpointManager()  # 默认 ~/.prisIrai/checkpoints
        wf_id = cm.new_workflow_id()
        cm.save(wf_id, "step1", {"ok": True, "result_path": "/tmp/x.mp4"})
        ...
        # 系统崩溃后
        ck = cm.load_latest(wf_id)  # 拿最近 step
        done = cm.resume_from(wf_id, all_step_ids)
    """

    def __init__(self, root: str | Path = DEFAULT_CHECKPOINT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ── 路径 ─────────────────────────────────────────────────
    def _wf_dir(self, workflow_id: str) -> Path:
        d = self.root / f"wf_{workflow_id}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _step_path(self, workflow_id: str, step_id: str) -> Path:
        return self._wf_dir(workflow_id) / f"step_{step_id}.json"

    # ── ID ──────────────────────────────────────────────────
    @staticmethod
    def new_workflow_id() -> str:
        """生成 workflow_id(用时间戳 + 4 位 hex)。"""
        import secrets
        return f"{int(time.time())}_{secrets.token_hex(4)}"

    # ── save ─────────────────────────────────────────────────
    def save(self, workflow_id: str, step_id: str, payload: dict[str, Any],
             *, status: str = "ok", error: str = "",
             attempt: int = 1) -> None:
        """原子落盘:写临时 + rename 避免半写文件。"""
        ck = Checkpoint(
            workflow_id=workflow_id,
            step_id=step_id,
            timestamp=time.time(),
            status=status,
            payload=payload,
            error=error,
            attempt=attempt,
        )
        path = self._step_path(workflow_id, step_id)
        # 原子写:tmpfile → rename
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ck_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(ck.to_dict(), f, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        except Exception:
            # 失败清理 tmp
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            raise
        log.debug("checkpoint saved: wf=%s step=%s status=%s",
                  workflow_id, step_id, status)

    # ── load ─────────────────────────────────────────────────
    def load_step(self, workflow_id: str, step_id: str) -> Optional[Checkpoint]:
        """读单个 step 的 checkpoint。文件不存在返 None。"""
        path = self._step_path(workflow_id, step_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Checkpoint.from_dict(data)
        except Exception as exc:  # noqa: BLE001
            log.warning("checkpoint load 失败 wf=%s step=%s: %s",
                        workflow_id, step_id, exc)
            return None

    def load_latest(self, workflow_id: str) -> Optional[Checkpoint]:
        """读最近写入的 step 的 checkpoint(按 timestamp)。"""
        wf_dir = self._wf_dir(workflow_id)
        if not wf_dir.exists():
            return None
        latest: Optional[Checkpoint] = None
        latest_ts = -1.0
        for path in wf_dir.glob("step_*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                ts = data.get("timestamp", 0)
                if ts > latest_ts:
                    latest_ts = ts
                    latest = Checkpoint.from_dict(data)
            except Exception as exc:  # noqa: BLE001
                log.warning("checkpoint parse 失败 %s: %s", path, exc)
        return latest

    # ── resume ───────────────────────────────────────────────
    def resume_from(self, workflow_id: str,
                    all_step_ids: list[str]) -> list[str]:
        """返已成功的 step id 列表(用于 workflow 跳过已完成 step)。

        只算 status=='ok' 的;status=='failed' 的当未完成(让 workflow 重做)。
        """
        done: list[str] = []
        for sid in all_step_ids:
            ck = self.load_step(workflow_id, sid)
            if ck and ck.status == "ok":
                done.append(sid)
        return done

    def load_step_payload(self, workflow_id: str, step_id: str) -> dict[str, Any]:
        """取已完成 step 的 payload(给 $ref 解析用)。失败返 {}。"""
        ck = self.load_step(workflow_id, step_id)
        return ck.payload if ck and ck.status == "ok" else {}

    # ── cleanup ──────────────────────────────────────────────
    def cleanup_lru(self, keep: int = 20) -> int:
        """LRU 清理:保留最近 N 个 workflow,删老的。返删除数。"""
        wfs = [d for d in self.root.iterdir() if d.is_dir() and d.name.startswith("wf_")]
        if len(wfs) <= keep:
            return 0
        # 按修改时间倒序
        wfs_sorted = sorted(wfs, key=lambda d: d.stat().st_mtime, reverse=True)
        to_delete = wfs_sorted[keep:]
        n = 0
        for d in to_delete:
            try:
                import shutil
                shutil.rmtree(d)
                n += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("checkpoint cleanup 失败 %s: %s", d, exc)
        return n

    # ── list(给 debug 用) ────────────────────────────────────
    def list_workflows(self) -> list[str]:
        """列所有 workflow_id。"""
        return sorted(d.name.removeprefix("wf_") for d in self.root.iterdir()
                      if d.is_dir() and d.name.startswith("wf_"))

    def list_steps(self, workflow_id: str) -> list[str]:
        """列某 workflow 的所有 step_id(按文件名)。"""
        wf_dir = self._wf_dir(workflow_id)
        if not wf_dir.exists():
            return []
        return sorted(p.stem.removeprefix("step_") for p in wf_dir.glob("step_*.json"))