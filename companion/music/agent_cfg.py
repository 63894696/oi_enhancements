# -*- coding: utf-8 -*-
"""
agent_cfg.py — M3.29.1 / M3.29.5 agent-only 配置系统

设计原则:
  - 全部配置 agent_only = True → 前端不暴露任何设置页面
  - 改配置只能走 REST POST /api/agent/cfg/set
  - 或走自然语言意图 /api/agent/intent(text="...")
  - schema 包含 type / range / values,自动校验

storage: <workdir>/_prisir_registry/music_cfg.json
       (单文件,内存缓存 + 写盘,简单 lock-free 用 _threading.Lock)
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("agent_cfg")

# 默认值
DEFAULTS: Dict[str, Any] = {
    # 歌词
    "lyrics.font_size":      32,             # int [12, 96]
    "lyrics.color":          "#f6f1e7",      # CSS color
    "lyrics.mode":           "line",         # line | word
    "lyrics.delay_ms":       0,              # int [-10000, 10000]
    "lyrics.opacity":        0.85,           # float [0, 1]
    "lyrics.window_visible": True,           # bool
    "lyrics.window_x":       200,            # int 屏幕坐标
    "lyrics.window_y":       600,            # int 屏幕坐标
    "lyrics.window_w":       720,
    "lyrics.window_h":       180,

    # 播放
    "playback.volume":       80,             # int [0, 100]
    "playback.mode":         "sequential",   # sequential | shuffle | repeat_one
    "playback.muted":        False,

    # 音乐库
    "music.root":            str(Path.home() / "Music"),  # path
    "music.source":          "auto",         # self | lx_desktop | auto
    "music.lyrics_provider": "auto",         # local | lrclib | auto

    # 歌单与种子
    "playlist.auto_seed":    True,
    "playlist.seed_max":     20,

    # 高级
    "lyrics.font_family":    "Noto Serif CJK SC, serif",
}


# schema:类型 + 范围/可选值
SCHEMA: Dict[str, Dict[str, Any]] = {
    "lyrics.font_size":      {"type": "int",   "range": (12, 96)},
    "lyrics.color":          {"type": "color"},
    "lyrics.mode":           {"type": "enum",  "values": ["line", "word"]},
    "lyrics.delay_ms":       {"type": "int",   "range": (-10000, 10000)},
    "lyrics.opacity":        {"type": "float", "range": (0.0, 1.0)},
    "lyrics.window_visible": {"type": "bool"},
    "lyrics.window_x":       {"type": "int"},
    "lyrics.window_y":       {"type": "int"},
    "lyrics.window_w":       {"type": "int",   "range": (320, 1920)},
    "lyrics.window_h":       {"type": "int",   "range": (120, 1080)},
    "lyrics.font_family":    {"type": "str",   "max_len": 100},

    "playback.volume":       {"type": "int",   "range": (0, 100)},
    "playback.mode":         {"type": "enum",  "values": ["sequential", "shuffle", "repeat_one"]},
    "playback.muted":        {"type": "bool"},

    "music.root":            {"type": "path"},
    "music.source":          {"type": "enum",  "values": ["self", "lx_desktop", "auto"]},
    "music.lyrics_provider": {"type": "enum",  "values": ["local", "lrclib", "auto"]},

    "playlist.auto_seed":    {"type": "bool"},
    "playlist.seed_max":     {"type": "int",   "range": (5, 100)},
}


# 自然语言意图路由(中文为主,英文兜底)
# 每条: (regex, handler)
#   handler 返回 (action, payload)
#     action="set"     → payload={path: value}
#     action="cmd"     → payload={"action": "<cmd_router action>", ...args}
NL_INTENTS: List[Tuple[re.Pattern, Any]] = [
    # 歌词字号
    (re.compile(r"歌词大点|字大点|字号.*大|字大一些|字大一点|font\s*size.*big|larger\s*font", re.I),
        lambda m: ("set", {"lyrics.font_size": 40})),
    (re.compile(r"歌词小点|字小点|字号.*小|字小一些|字小一点|smaller\s*font", re.I),
        lambda m: ("set", {"lyrics.font_size": 24})),
    (re.compile(r"歌词.*?(\d+).*?号(?:字)?|字号\s*(\d+)|font\s*size\s*(\d+)", re.I),
        lambda m: ("set", {"lyrics.font_size": _clamp_int(
            int(m.group(1) or m.group(2) or m.group(3) or 32), 12, 96)})),

    # 音量
    (re.compile(r"声音轻点|声音轻一点|声音轻些|音量.*小|音量.*低|小声点|小声儿|小点声|quiet|softer|volume\s*down|lower\s*volume", re.I),
        lambda m: ("set", {"playback.volume": 60})),
    (re.compile(r"声音大点|声音大一点|声音大些|音量.*大|音量.*高|大声点|大声儿|大点声|louder|volume\s*up", re.I),
        lambda m: ("set", {"playback.volume": 100})),
    (re.compile(r"音量\s*(\d+)|volume\s*(\d+)", re.I),
        lambda m: ("set", {"playback.volume": _clamp_int(
            int(m.group(1) or m.group(2) or 80), 0, 100)})),
    (re.compile(r"静音|mute", re.I),
        lambda m: ("set", {"playback.muted": True})),
    (re.compile(r"取消静音|unmute", re.I),
        lambda m: ("set", {"playback.muted": False})),

    # 播放模式
    (re.compile(r"循环播放|单曲循环|repeat\s*one|repeat_one", re.I),
        lambda m: ("set", {"playback.mode": "repeat_one"})),
    (re.compile(r"列表循环|顺序播放|sequential", re.I),
        lambda m: ("set", {"playback.mode": "sequential"})),
    (re.compile(r"随机播放|shuffle|随机", re.I),
        lambda m: ("set", {"playback.mode": "shuffle"})),

    # 控制
    (re.compile(r"换下一首|下一首|切歌|next|skip", re.I),
        lambda m: ("cmd", {"action": "next"})),
    (re.compile(r"上一首|切回|prev|previous", re.I),
        lambda m: ("cmd", {"action": "prev"})),
    (re.compile(r"暂停|pause", re.I),
        lambda m: ("cmd", {"action": "pause"})),
    (re.compile(r"继续|恢复|resume|play", re.I),
        lambda m: ("cmd", {"action": "resume"})),
    (re.compile(r"停止|stop", re.I),
        lambda m: ("cmd", {"action": "stop"})),

    # 歌词显示
    (re.compile(r"隐藏歌词|歌词关|关掉歌词|lyrics\s*hide|hide\s*lyrics", re.I),
        lambda m: ("set", {"lyrics.window_visible": False})),
    (re.compile(r"显示歌词|歌词开|打开歌词|lyrics\s*show|show\s*lyrics", re.I),
        lambda m: ("set", {"lyrics.window_visible": True})),
    (re.compile(r"逐字|word\s*mode|逐字歌词", re.I),
        lambda m: ("set", {"lyrics.mode": "word"})),
    (re.compile(r"逐行|line\s*mode|整行歌词", re.I),
        lambda m: ("set", {"lyrics.mode": "line"})),
    (re.compile(r"歌词.*?提前.*?(\d+)|lyrics\s*delay.*?(-?\d+)|歌词.*?延后.*?(\d+)", re.I),
        lambda m: ("set", {"lyrics.delay_ms": _clamp_int(
            int(m.group(1) or m.group(2) or m.group(3) or 0), -10000, 10000)})),

    # 搜歌 / 播 X — 注意顺序:长的具体的优先,简单「放 X」放后面
    # 1) 长的:「放/播 X 的歌」「按 X 歌单放」「搜 X」「找 X」
    (re.compile(r"按\s*(.+?)\s*歌\s*[单列]?(?:播|放|来)?$|按\s*(.+?)\s*的?\s*歌\s*单\s*(?:放|播|来)?$|放\s*(.+?)\s*的?\s*歌|播\s*(.+?)\s*的?\s*歌|搜\s*(?:歌\s*)?(.+)|找\s*(?:歌\s*)?(.+)|play\s+(.+)|search\s+(.+)", re.I),
        lambda m: ("cmd", {"action": "search",
                          "query": (m.group(1) or m.group(2) or m.group(3) or m.group(4) or
                                    m.group(5) or m.group(6) or m.group(7) or m.group(8) or "").strip()})),
    # 2) 简版:「放/播/听 X」(不带「的歌」)— 注意前面不要带「的」「一首歌」等修饰
    (re.compile(r"^(?:放|播|听)\s*([^\s的].+?)\s*$", re.I),
        lambda m: ("cmd", {"action": "search",
                          "query": m.group(1).strip()})),
]


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def validate(path: str, value: Any) -> Tuple[bool, str]:
    """校验单个 cfg。返 (ok, err_msg)。"""
    if path not in SCHEMA:
        return False, f"unknown path: {path}"
    spec = SCHEMA[path]
    t = spec["type"]
    try:
        if t == "int":
            v = int(value)
            if "range" in spec:
                lo, hi = spec["range"]
                if not (lo <= v <= hi):
                    return False, f"int out of range [{lo},{hi}]: {v}"
        elif t == "float":
            v = float(value)
            if "range" in spec:
                lo, hi = spec["range"]
                if not (lo <= v <= hi):
                    return False, f"float out of range [{lo},{hi}]: {v}"
        elif t == "bool":
            if isinstance(value, bool):
                pass
            elif isinstance(value, str):
                if value.lower() not in ("true", "false", "1", "0"):
                    return False, f"bool str invalid: {value}"
            elif isinstance(value, int) and value in (0, 1):
                pass
            else:
                return False, f"bool invalid: {value!r}"
        elif t == "enum":
            if value not in spec.get("values", []):
                return False, f"enum must be one of {spec.get('values')}, got {value!r}"
        elif t == "color":
            if not isinstance(value, str) or not re.match(r"^#[0-9a-fA-F]{3,8}$", value.strip()):
                return False, f"color must be #hex, got {value!r}"
        elif t == "path":
            if not isinstance(value, str):
                return False, f"path must be str, got {type(value).__name__}"
        elif t == "str":
            if not isinstance(value, str):
                return False, f"str invalid: {value!r}"
            if "max_len" in spec and len(value) > spec["max_len"]:
                return False, f"str too long (>{spec['max_len']})"
    except (ValueError, TypeError) as e:
        return False, f"type coerce fail: {e}"
    return True, ""


class AgentCfg:
    """线程安全配置存储 + 自然语言意图路由。

    用法:
        cfg = AgentCfg(workdir=Path("/path"))
        cfg.set("lyrics.font_size", 40)           # 写
        v = cfg.get("lyrics.font_size")            # 读
        cfg.list()                                  # 全列(给 agent)
        res = cfg.handle_intent("歌词大点")          # 自然语言
    """

    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self._cfg_file = self.workdir / "_prisir_registry" / "music_cfg.json"
        self._cfg_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._values: Dict[str, Any] = dict(DEFAULTS)
        self._load()

    def _load(self) -> None:
        if not self._cfg_file.exists():
            return
        try:
            data = json.loads(self._cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # 只接受已知 path(防老 schema 残留)
                for k, v in data.items():
                    if k in SCHEMA:
                        self._values[k] = v
        except Exception as e:  # noqa: BLE001
            log.warning("[agent_cfg] load fail: %s; using defaults", e)

    def _save(self) -> None:
        try:
            self._cfg_file.write_text(
                json.dumps(self._values, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            log.warning("[agent_cfg] save fail: %s", e)

    def get(self, path: str, default: Any = None) -> Any:
        with self._lock:
            if path in self._values:
                return self._values[path]
            if path in DEFAULTS:
                return DEFAULTS[path]
            return default

    def get_all(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def list(self) -> List[Dict[str, Any]]:
        """列出全部配置(path + value + type + range/values),给 agent 列示。"""
        with self._lock:
            items = []
            for path, spec in SCHEMA.items():
                item = {
                    "path": path,
                    "value": self._values.get(path, DEFAULTS.get(path)),
                    "type": spec["type"],
                    "agent_only": True,
                }
                if "range" in spec:
                    item["range"] = list(spec["range"])
                if "values" in spec:
                    item["values"] = list(spec["values"])
                if "max_len" in spec:
                    item["max_len"] = spec["max_len"]
                items.append(item)
            return items

    def set(self, path: str, value: Any) -> Tuple[bool, str]:
        ok, err = validate(path, value)
        if not ok:
            return False, err
        # 归一化 bool
        spec = SCHEMA[path]
        t = spec["type"]
        if t == "bool":
            if isinstance(value, str):
                value = value.lower() in ("true", "1")
            elif isinstance(value, int):
                value = bool(value)
        elif t == "int":
            value = int(value)
        elif t == "float":
            value = float(value)
        with self._lock:
            self._values[path] = value
            self._save()
        return True, ""

    def set_many(self, kv: Dict[str, Any]) -> Dict[str, Any]:
        """批量 set;返每条结果。"""
        return {k: self.set(k, v) for k, v in kv.items()}

    def handle_intent(self, text: str) -> Dict[str, Any]:
        """自然语言意图路由。返 {matched, action, payload, raw}。

        不匹配的返 matched=False,调用方应让 agent 走 LLM tool calling 路径。
        """
        text = (text or "").strip()
        if not text:
            return {"matched": False, "raw": text}

        for pat, handler in NL_INTENTS:
            m = pat.search(text)
            if m:
                try:
                    action, payload = handler(m)
                    return {
                        "matched": True,
                        "action": action,
                        "payload": payload,
                        "raw": text,
                    }
                except Exception as e:  # noqa: BLE001
                    log.warning("[agent_cfg] intent handler err: %s", e)
                    return {"matched": False, "raw": text, "err": str(e)}
        return {"matched": False, "raw": text}


def quick_smoke() -> Dict[str, Any]:
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        cfg = AgentCfg(Path(td))
        # 测 set
        cfg.set("lyrics.font_size", 48)
        cfg.set("playback.volume", 70)
        # 测 nl_intent
        r1 = cfg.handle_intent("歌词大点")
        r2 = cfg.handle_intent("声音轻一点")
        r3 = cfg.handle_intent("换下一首")
        r4 = cfg.handle_intent("今天天气不错")  # 不匹配
        return {
            "set_ok": cfg.get("lyrics.font_size"),
            "nl_intent_1": r1,
            "nl_intent_2": r2,
            "nl_intent_3": r3,
            "nl_intent_4": r4,
        }


if __name__ == "__main__":
    import json
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(quick_smoke(), ensure_ascii=False, indent=2))
