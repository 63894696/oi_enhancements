"""prisir_work/easel_bridge.py — Easel 子进程桥接层。

定位:不 `pip install easel`(那会拉 25+ 依赖 + Playwright + Chromium + Chrome ext),
而是按需 `subprocess.run()` 调 Easel 仓库里几个 Python 脚本入口,把公众号扫码发布 +
数据回收 + AI 反检测 + MD→HTML 排版暴露成 PrisirWork 能力。

Easel 默认装路径(EASEL_ROOT 环境变量 / settings.json / 启发式探测顺序):
  1. 环境变量 EASEL_ROOT
  2. settings.json 里的 `easel.root`
  3. 已知候选:
     ~/work/zju_easel              (我们刚 clone 的位置)
     ~/Easel
     ~/projects/Easel
     C:/work/zju_easel             (Windows)
     C:/Users/Administrator/work/zju_easel

依赖(本包直接用的三方库):无(纯 stdlib: subprocess/json/pathlib/dataclasses)。
Easel 端依赖(用户自备,装在 Easel 仓库的 venv / 全局):
  playwright>=1.45     (扫码登录 + mp 后台抓取)
  Pillow, opencv-python
  PyYAML
  markdown, jieba, snownlp
  faster-whisper, edge-tts

Easel 脚本入口(子命令 → 入口脚本):
  login     : skills/shared/scripts/weixin_mp_stats.py
  whoami    : skills/shared/scripts/weixin_mp_stats.py
  stats     : skills/shared/scripts/weixin_mp_stats.py
  publish   : skills/openclaw/skill-wechat-publisher/scripts/publish.py
  ai_score  : skills/openclaw/skill-wechat-publisher/scripts/ai_score.py
  md2html   : skills/openclaw/skill-wechat-publisher/scripts/html_converter.py

约定:
  · 每个 _run() 都吃 timeout,失败兜底返空 dict(不抛栈);wrapper 层不破窗。
  · 路径 / 文件 / 二进制参数都校验存在后再调,缺失返 ok=False + 明确 stderr。
  · whoami 5 秒拉一次本地状态缓存,避免每请求都开 Playwright。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "EaselNotFoundError",
    "EaselBridge",
    "easel",
    "find_easel_root",
    "EaselResult",
]

# ---------------------------------------------------------------------------
# 路径探测
# ---------------------------------------------------------------------------

_CANDIDATE_ROOTS = [
    Path("C:/Users/Administrator/work/zju_easel"),
    Path("C:/work/zju_easel"),
    Path.home() / "work" / "zju_easel",
    Path.home() / "Easel",
    Path.home() / "projects" / "Easel",
]


def _load_settings_root() -> str | None:
    """读 .claude/settings.json 里的 easel.root(若用户显式配置)。"""
    try:
        sp = Path.home() / ".claude" / "settings.json"
        if not sp.is_file():
            return None
        data = json.loads(sp.read_text(encoding="utf-8"))
        v = data.get("easel", {}).get("root")
        return v if isinstance(v, str) else None
    except Exception:
        return None


def find_easel_root() -> Path | None:
    """EASEL_ROOT → settings → 启发式候选。返回存在的仓库根。"""
    env = os.environ.get("EASEL_ROOT")
    if env:
        p = Path(env)
        if p.is_dir() and (p / "skills").is_dir():
            return p.resolve()
    cfg = _load_settings_root()
    if cfg:
        p = Path(cfg)
        if p.is_dir() and (p / "skills").is_dir():
            return p.resolve()
    for cand in _CANDIDATE_ROOTS:
        if cand.is_dir() and (cand / "skills").is_dir():
            return cand.resolve()
    return None


# ---------------------------------------------------------------------------
# 结果 dataclass
# ---------------------------------------------------------------------------


@dataclass
class EaselResult:
    """统一返 ok/rc/stderr/stdout/parsed。"""
    ok: bool
    rc: int = 0
    stderr: str = ""
    stdout: str = ""
    parsed: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "rc": self.rc,
            "error": self.error,
            "parsed": self.parsed,
            "stderr_tail": self.stderr[-500:] if self.stderr else "",
            "stdout_tail": self.stdout[-500:] if self.stdout else "",
        }


class EaselNotFoundError(FileNotFoundError):
    """未找到 Easel 仓库根或关键脚本。"""


# ---------------------------------------------------------------------------
# 子进程包装
# ---------------------------------------------------------------------------


class EaselBridge:
    """Easel 子进程桥接。

    用法:
        eb = EaselBridge()              # 自动探测 root
        if not eb.ready: ...            # 缺 Easel,降级处理
        r = eb.whoami()                 # 检查登录态
        r = eb.stats(days=30)           # 数据回收
        r = eb.publish_html(html, title, cover_path, digest="...")
        r = eb.ai_score(text)
        r = eb.md2html(md_path)
    """

    def __init__(self, root: str | os.PathLike | None = None) -> None:
        self._root = Path(root).resolve() if root else find_easel_root()
        self._ready = bool(self._root and self._check_scripts())
        self._whoami_cache: dict[str, Any] = {}
        self._whoami_ts = 0.0

    @property
    def root(self) -> Path | None:
        return self._root

    @property
    def ready(self) -> bool:
        return self._ready

    # ----- 内部 -----
    def _script(self, rel: str) -> Path:
        if not self._root:
            raise EaselNotFoundError("Easel 根目录未找到")
        return self._root / rel

    def _check_scripts(self) -> bool:
        """5 个关键脚本全在才算 ready。"""
        if not self._root:
            return False
        needed = [
            "skills/shared/scripts/weixin_mp_stats.py",
            "skills/openclaw/skill-wechat-publisher/scripts/publish.py",
            "skills/openclaw/skill-wechat-publisher/scripts/ai_score.py",
            "skills/openclaw/skill-wechat-publisher/scripts/html_converter.py",
        ]
        return all((self._root / n).is_file() for n in needed)

    def _run(self, rel_script: str, args: list[str],
             *, timeout: int = 120, cwd: bool = True,
             input_text: str | None = None) -> EaselResult:
        """调 python <rel_script> <args>;cwd 默认落 Easel 根(Easel 脚本里多用相对路径)。

        不抛栈:任何异常 / 非零 rc → ok=False + error 字段。
        """
        if not self._root:
            return EaselResult(ok=False, error="Easel 根目录未找到")
        script = self._script(rel_script)
        if not script.is_file():
            return EaselResult(
                ok=False, error=f"脚本缺失: {rel_script}")
        cmd = [sys.executable, str(script), *args]
        try:
            r = subprocess.run(
                cmd,
                input=input_text,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self._root) if cwd else None,
            )
        except subprocess.TimeoutExpired:
            return EaselResult(ok=False, error=f"timeout {timeout}s")
        except Exception as e:  # noqa: BLE001
            return EaselResult(ok=False, error=f"{type(e).__name__}: {e}")
        # 解析 stdout 末段 JSON(脚本都习惯最后 print JSON)
        parsed: dict[str, Any] = {}
        try:
            # 取最后一行非空当 JSON
            for line in reversed((r.stdout or "").splitlines()):
                line = line.strip()
                if line.startswith("{") and line.endswith("}"):
                    parsed = json.loads(line)
                    break
        except Exception:
            parsed = {}
        return EaselResult(
            ok=(r.returncode == 0),
            rc=r.returncode,
            stderr=r.stderr or "",
            stdout=r.stdout or "",
            parsed=parsed,
            error="" if r.returncode == 0 else f"rc={r.returncode}",
        )

    # ----- 公开命令 -----

    def whoami(self, *, force: bool = False) -> EaselResult:
        """登录态检查。5s 缓存。"""
        now = time.time()
        if not force and (now - self._whoami_ts) < 5.0 and self._whoami_cache:
            return EaselResult(ok=True, parsed=self._whoami_cache)
        r = self._run("skills/shared/scripts/weixin_mp_stats.py",
                      ["whoami"], timeout=30)
        if r.ok:
            self._whoami_cache = r.parsed
            self._whoami_ts = now
        return r

    def login(self) -> EaselResult:
        """触发扫码登录(用户需盯着 mp.weixin.qq.com 出二维码)。

        实际不会阻塞等用户扫码 — 这里是 fire-and-forget。返回的 parsed.ok 通常 false
        因为扫码没完成;真状态以后续 whoami 为准。
        """
        # login 在扫码前会阻塞,timeout 给短一些,真扫码由用户手动跑 weixin_mp_stats.py login
        r = self._run("skills/shared/scripts/weixin_mp_stats.py",
                      ["login"], timeout=15)
        return r

    def stats(self, *, count: int = 30) -> EaselResult:
        """公众号数据回收(发表记录 + metrics/notes/growth)。

        Easel weixin_mp_stats.py stats 接受的参数:
          --count N       列表条数(默认 30)
          --headed        显示浏览器(调试)
          --proxy URL     受限网络代理
        没有 --days — stats 自带 time series(growth: last/day/week/month/year)。
        """
        r = self._run("skills/shared/scripts/weixin_mp_stats.py",
                      ["stats", "--count", str(count)], timeout=120)
        return r

    def publish_html(self, html_path: str, *, title: str,
                     cover: str, digest: str = "",
                     author: str = "") -> EaselResult:
        """用 Easel 会话模式(默认走 mp 后台扫码登录)发草稿。

        入参:
          html_path : 已排版好的 HTML 文件路径
          title     : 文章标题
          cover     : 封面图绝对路径
          digest    : 摘要(空则脚本从正文抽)
          author    : 署名(Easel 多账号 YAML 配置的 author 名;空则用当前会话号)
        """
        if not Path(html_path).is_file():
            return EaselResult(ok=False, error=f"html 文件不存在: {html_path}")
        if not Path(cover).is_file():
            return EaselResult(ok=False, error=f"封面图不存在: {cover}")
        args = ["--html", html_path, "--title", title, "--cover", cover]
        if digest:
            args += ["--digest", digest]
        if author:
            args += ["--author", author]
        return self._run(
            "skills/openclaw/skill-wechat-publisher/scripts/publish.py",
            args, timeout=300)

    def ai_score(self, text: str) -> EaselResult:
        """AI 味评分。text 走 stdin 喂给 ai_score.py。"""
        return self._run(
            "skills/openclaw/skill-wechat-publisher/scripts/ai_score.py",
            [], timeout=60, cwd=False, input_text=text)

    def md2html(self, md_path: str, *, theme: str = "") -> EaselResult:
        """Markdown → 公众号 HTML。输出 HTML 路径在 r.parsed.html_path 里。"""
        if not Path(md_path).is_file():
            return EaselResult(ok=False, error=f"md 文件不存在: {md_path}")
        args = [md_path]
        if theme:
            args += ["--theme", theme]
        return self._run(
            "skills/openclaw/skill-wechat-publisher/scripts/html_converter.py",
            args, timeout=60)

    # ----- 小红书(Easel skill-xhs-publisher via shared/xhs_publish.py) -----

    def xhs_whoami(self) -> EaselResult:
        """小红书登录态。"""
        return self._run("skills/shared/scripts/xhs_publish.py",
                         ["whoami"], timeout=60)

    def xhs_login(self) -> EaselResult:
        """小红书扫码登录(异步 fire-and-forget,通常 timeout 内拿到二维码即返)。"""
        return self._run("skills/shared/scripts/xhs_publish.py",
                         ["login"], timeout=20)

    def xhs_publish(self, *, title: str, content: str,
                    images: str = "", video: str = "",
                    tags: str = "", exec_real: bool = False) -> EaselResult:
        """小红书发布图文/视频。

        exec_real=False(默认)是 dry-run,只校验参数不真发;exec_real=True 才真发布。
        images 走 images="img1.jpg,img2.jpg" 形式;video 单文件路径。
        """
        args = ["publish", "--title", title, "--content", content]
        if images:
            args += ["--images", images]
        if video:
            args += ["--video", video]
        if tags:
            args += ["--tags", tags]
        if exec_real:
            args += ["--exec"]
        return self._run("skills/shared/scripts/xhs_publish.py",
                         args, timeout=300)

    # ----- B站(Easel skill-bilibili-upload 包装 biliup CLI) -----

    def bili_login(self) -> EaselResult:
        """B站扫码登录(返回 cookie 给后续投稿复用)。"""
        return self._run("skills/shared/scripts/bili_login.py",
                         ["login"], timeout=20)

    def bili_upload(self, *, video: str, title: str = "",
                    partition: str = "", tid: int | None = None,
                    desc: str = "", tag: str = "",
                    cover: str = "", copyright: int = 1,
                    exec_real: bool = False) -> EaselResult:
        """B站投稿(走 biliup CLI)。

        exec_real=False(默认)dry-run;exec_real=True 真投稿。
        """
        if not Path(video).is_file():
            return EaselResult(ok=False, error=f"视频文件不存在: {video}")
        args = ["upload", "--video", video, "--copyright", str(copyright)]
        if title:
            args += ["--title", title]
        if partition:
            args += ["--partition", partition]
        if tid is not None:
            args += ["--tid", str(tid)]
        if desc:
            args += ["--desc", desc]
        if tag:
            args += ["--tag", tag]
        if cover:
            args += ["--cover", cover]
        if exec_real:
            args += ["--exec"]
        return self._run(
            "skills/openclaw/skill-bilibili-upload/scripts/bili_upload.py",
            args, timeout=300)


# ---------------------------------------------------------------------------
# 单例 + 健康检查
# ---------------------------------------------------------------------------

_singleton: EaselBridge | None = None


def easel(*, force_reload: bool = False) -> EaselBridge:
    """返回单例(模块级)。ready=False 时仍可用,只是每个调用都会返 ok=False。"""
    global _singleton
    if force_reload or _singleton is None:
        _singleton = EaselBridge()
    return _singleton


# ---------------------------------------------------------------------------
# CLI 自检
# ---------------------------------------------------------------------------

def _cli(argv: list[str]) -> int:
    import argparse
    p = argparse.ArgumentParser(prog="prisirmp-easel-bridge",
                                description="Easel 子进程桥接自检")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("path", help="打印探测到的 Easel 根")
    sub.add_parser("whoami", help="检查 mp 扫码登录态")
    sub.add_parser("stats", help="拉公众号近 30 天数据")
    ai = sub.add_parser("ai-score", help="AI 味评分")
    ai.add_argument("text_file", help="要评分的文本文件")

    args = p.parse_args(argv)
    eb = easel(force_reload=True)
    if not eb.ready:
        print(f"[error] Easel 未就绪。root={eb.root}")
        print("        设置 EASEL_ROOT 或把 Easel 放到 ~/work/zju_easel/")
        return 2
    if args.cmd == "path":
        print(eb.root)
    elif args.cmd == "whoami":
        r = eb.whoami(force=True)
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    elif args.cmd == "stats":
        r = eb.stats()
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    elif args.cmd == "ai-score":
        text = Path(args.text_file).read_text(encoding="utf-8")
        r = eb.ai_score(text)
        print(json.dumps(r.to_dict(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv[1:]))