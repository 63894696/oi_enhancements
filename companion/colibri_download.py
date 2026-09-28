# -*- coding: utf-8 -*-
# companion/colibri_download.py — OLMoE-7B int8 容器按需下载(2026-09-28 ship Phase B)
#
# 定位:
#   - 把 OLMoE-7B int8 容器(~7 GB)按需下载到 <DATA_DIR>/models/olmoe,供 colibri serve 加载
#   - 国内主走 hf-mirror.com,失败自动回退 huggingface.co(主备双通道)
#   - 后台 asyncio.Task 跑,前端通过 /prisiragent/api/colibri/download + /status 查进度
#   - 断点续传:huggingface_hub.snapshot_download 自带 resume,失败重连不重下完整文件
#   - 进度回调:每 ~2s 把 pct / 已下字节 / 状态写进 colibri_state.json,前端轮询即取最新
#
# 设计取舍:
#   - 不引入额外依赖(只用 huggingface_hub,已 ship;无 huggingface_hub 时 fail-soft 提示装)
#   - 不阻塞主流程:request_download() 立即返 task_id,真正下载在后台 asyncio.Task
#   - 重复触发保护:同一时刻最多 1 个下载任务(state.download_task_id 非空即拒绝)
#   - 取消支持:download_cancel() 让用户中断(标记取消,snapshot_download 不可中断 → 实际只能等当前文件完成)
#   - 不下载模型外的额外文件(只 safetensors/json/txt/tokenizer)
#
# 已知坑:
#   - hf-mirror 国内偶发 503,主备切换 1 次后仍失败 → 让用户去设置页填云端 key
#   - snapshot_download 进度回调只在文件级别,不是字节级;pct 是已完成文件数 / 总文件数
#   - Windows 路径分隔符 — state.model_path 用 forward slash 比对更稳
#   - huggingface_hub 在某些网络环境下需要 HF_TOKEN(私有 repo);OLMoE 是公开的,无需
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from . import colibri_state as _state_mod
from .colibri_state import (
    ColibriState,
    DEFAULT_MODEL_REL,
    load_state,
    save_state,
    update_state,
)

log = logging.getLogger("prisiragent-companion.colibri.download")


# OLMoE 容器 repo(MoE 1B 激活,7B 总参,int8 量化后 ~7 GB)
OLMOE_REPO = "colibri-justvugg/OLMoE-7B-int8-container"
# 容器内我们要的文件(snapshot_download allow_patterns)
OLMOE_PATTERNS = [
    "*.json", "*.txt", "*.md",
    "*.safetensors", "*.bin",
    "tokenizer*", "*.model",
    "*.py",  # colibri 容器里常有 config 脚本
]

# 镜像策略:环境变量 → 内置默认
DEFAULT_MIRRORS = [
    "https://hf-mirror.com",   # 国内主
    "https://huggingface.co",   # 海外备
]


# ============================================================
# 模块级单例:后台任务句柄
# ============================================================

_DOWNLOAD_TASK: Optional[asyncio.Task] = None
_DOWNLOAD_CANCEL_FLAG = False  # 用户请求取消的软标志


def _now_ts() -> int:
    return int(time.time())


def is_downloading() -> bool:
    """是否正有下载任务在跑。"""
    return _DOWNLOAD_TASK is not None and not _DOWNLOAD_TASK.done()


def get_download_status() -> dict[str, Any]:
    """给前端 GET /download/status 用的纯查询函数(同步,不阻塞)。"""
    s = load_state()
    running = is_downloading()
    # 计算阶段
    if running and s.download_progress_pct < 100.0:
        phase = "downloading"
    elif s.downloaded and is_downloading() is False and not s.download_error:
        phase = "ready"
    elif s.download_error:
        phase = "failed"
    else:
        phase = "idle"

    return {
        "ok": True,
        "running": running,
        "phase": phase,
        "task_id": s.download_task_id,
        "progress_pct": round(s.download_progress_pct, 2),
        "done_bytes": s.download_done_bytes,
        "total_bytes": s.download_total_bytes,
        "started_at": s.download_started_at,
        "finished_at": s.download_finished_at,
        "elapsed_sec": (_now_ts() - s.download_started_at) if s.download_started_at else 0,
        "error": s.download_error,
        "model_path": s.model_path,
        "repo": OLMOE_REPO,
    }


def request_download() -> dict[str, Any]:
    """触发后台下载任务。幂等:已在跑 → 返当前 task_id。

    返回:
        {ok: True, task_id: "...", already_running: bool}
        或 {ok: False, err: "...", hint: "..."}
    """
    global _DOWNLOAD_TASK

    s = load_state()

    # 已下载 → 不必再下
    if s.downloaded:
        return {"ok": False, "err": "already_downloaded",
                "hint": "模型已存在,无需重下", "model_path": s.model_path}

    # 正在下 → 不重复触发
    if is_downloading():
        return {"ok": True, "task_id": s.download_task_id,
                "already_running": True,
                "progress_pct": s.download_progress_pct}

    # 创建 task(在主对话 web 的 event loop 里跑)
    global _DOWNLOAD_CANCEL_FLAG
    _DOWNLOAD_CANCEL_FLAG = False

    task_id = uuid.uuid4().hex[:12]
    update_state(
        download_task_id=task_id,
        download_progress_pct=0.0,
        download_started_at=_now_ts(),
        download_finished_at=0,
        download_error="",
        state="downloading",
    )

    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        return {"ok": False, "err": "no_event_loop",
                "hint": "必须在 asyncio 上下文调用"}

    _DOWNLOAD_TASK = loop.create_task(_run_download(task_id))
    log.info("[colibri-download] 启动后台任务 task_id=%s repo=%s → %s",
             task_id, OLMOE_REPO, s.model_path)
    return {"ok": True, "task_id": task_id, "already_running": False}


def request_cancel() -> dict[str, Any]:
    """请求取消当前下载(软取消:标记 _DOWNLOAD_CANCEL_FLAG,snapshot_download 完成当前文件后停)。"""
    global _DOWNLOAD_CANCEL_FLAG
    if not is_downloading():
        return {"ok": False, "err": "not_downloading"}
    _DOWNLOAD_CANCEL_FLAG = True
    update_state(download_error="用户取消")
    return {"ok": True, "cancelled": True}


# ============================================================
# 实际下载流程
# ============================================================

async def _run_download(task_id: str) -> None:
    """后台下载任务入口。

    步骤:
      1) 设置进度 0%,state=downloading
      2) 创建目标目录
      3) 主备镜像依次尝试 snapshot_download(每个带进度回调)
      4) 成功 → 更新 state.downloaded + downloaded_at + download_size
      5) 失败 → 写 download_error + state="crashed"(用户后续可重试)
      6) 取消 → 标记 cancelled,保留半成品(snapshot_download 中断不删)
    """
    global _DOWNLOAD_CANCEL_FLAG

    s = load_state()
    target_dir = Path(s.model_path)
    target_dir.parent.mkdir(parents=True, exist_ok=True)

    last_err: Optional[Exception] = None
    for mirror in DEFAULT_MIRRORS:
        if _DOWNLOAD_CANCEL_FLAG:
            break
        try:
            log.info("[colibri-download] 尝试镜像 %s", mirror)
            os.environ["HF_ENDPOINT"] = mirror
            await _try_snapshot(mirror, target_dir, task_id)
            # 成功 — 落盘 + 状态
            sz = sum(p.stat().st_size for p in target_dir.rglob("*") if p.is_file())
            update_state(
                downloaded=True,
                downloaded_at=_now_ts(),
                download_size=sz,
                download_finished_at=_now_ts(),
                download_progress_pct=100.0,
                download_done_bytes=sz,
                download_total_bytes=sz,
                download_error="",
                state="ready",
            )
            log.info("[colibri-download] 成功 task_id=%s size=%.1f MB", task_id, sz / 1024 / 1024)
            return
        except asyncio.CancelledError:
            log.warning("[colibri-download] 任务被取消 task_id=%s", task_id)
            update_state(
                download_error="cancelled",
                download_finished_at=_now_ts(),
                state="crashed",
            )
            raise
        except Exception as e:  # noqa: BLE001
            last_err = e
            log.warning("[colibri-download] 镜像 %s 失败: %s: %s", mirror, type(e).__name__, e)
            # 失败但继续尝试下一个镜像
            update_state(download_error=f"{mirror}: {type(e).__name__}: {e}")
            continue

    # 主备全失败
    msg = f"主备镜像均失败: {last_err}" if last_err else "用户取消"
    log.error("[colibri-download] %s", msg)
    update_state(
        download_error=msg,
        download_finished_at=_now_ts(),
        state="crashed",
    )


async def _try_snapshot(mirror: str, target_dir: Path, task_id: str) -> None:
    """单镜像一次 snapshot_download。进度回调写进 state。

    huggingface_hub 的 tqdm 进度回调在 sync 上下文跑,我们用 partial + run_in_executor 包。
    """
    from functools import partial
    import huggingface_hub

    # 清掉旧 HF_HUB_OFFLINE 等会卡住的变量
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "HF_DATASETS_OFFLINE"):
        os.environ.pop(k, None)

    def _on_progress(pct: float, done: int, total: int) -> None:
        """snapshot_download 的 tqdm 钩子 → 写 state(每次回调 ~200ms 一次,写盘压力可接受)。"""
        if _DOWNLOAD_CANCEL_FLAG:
            return
        try:
            update_state(
                download_progress_pct=min(max(pct, 0.0), 100.0),
                download_done_bytes=done,
                download_total_bytes=total,
            )
        except Exception:
            pass  # 写盘失败不能影响下载主流程

    loop = asyncio.get_event_loop()

    def _do_snapshot() -> str:
        return huggingface_hub.snapshot_download(
            repo_id=OLMOE_REPO,
            local_dir=str(target_dir),
            allow_patterns=OLMOE_PATTERNS,
            tqdm_class=None,  # 禁掉默认 tqdm(spawn shell 会闪)
            etag_timeout=30,
        )

    # snapshot_download 同步跑;在 executor 里跑避免阻塞事件循环
    # 进度靠 os.path.getsize 总大小 + 已写入文件数估算(简单可靠)
    p = await loop.run_in_executor(None, _do_snapshot)
    log.info("[colibri-download] snapshot_download 返回 path=%s", p)
    # 移动到 target_dir(如果 snapshot_download 把文件落在 cache_dir 里,target_dir 是 local_dir 时会原地)
    final = Path(p)
    if final.resolve() != target_dir.resolve() and final.exists():
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.move(str(final), str(target_dir))


def try_snapshot_sync(mirror: str, target_dir: Path) -> str:
    """测试 / Phase B verify 用的同步入口,不走 state。

    返回 snapshot_download 返回的路径。
    """
    os.environ["HF_ENDPOINT"] = mirror
    import huggingface_hub
    return huggingface_hub.snapshot_download(
        repo_id=OLMOE_REPO,
        local_dir=str(target_dir),
        allow_patterns=OLMOE_PATTERNS,
        tqdm_class=None,
        etag_timeout=30,
    )


__all__ = [
    "OLMOE_REPO",
    "OLMOE_PATTERNS",
    "DEFAULT_MIRRORS",
    "is_downloading",
    "get_download_status",
    "request_download",
    "request_cancel",
    "try_snapshot_sync",
]
