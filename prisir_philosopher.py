# -*- coding: utf-8 -*-
"""prisir_philosopher.py — 哲人模式:多学派×多引擎思想碰撞(2026-09-05)

设计(对齐用户拍板):
- 学派是「角色」,模型是「嗓子」。学派面板(角色提示词)与引擎(可调模型)解耦。
- 引擎注册表内置实测数据(base_url/key_env/候选模型/出字字段);启动探测降级:
  402 订阅墙 / 429 限流 / 超时 → 标不可用,不进面板;M=1 单引擎分饰,M=0 提示配 key。
- key 只从环境变量读,绝不回显、不落盘、不出本机。
- 三轮:R1 各学派并行立场+建议 → R2 学派互见观点后回应(辩论)→ R3 综述(共识/分歧/行动)。
- 真实坑内置:思维链模型(Kimi/GLM/nemotron/gpt-oss)会把思考当正文,需剥离;
  MiniMax 带 <think> 段;部分模型出字在 reasoning/reasoning_content 字段。
"""
from __future__ import annotations

import json
import os
import re
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor, as_completed

# ─────────────────────────────────────────────────────────────
# 引擎注册表(2026-09-05 本机实测数据;key_env 从环境读,缺 key 即不可用)
#   models: 候选模型按优先级序;thinking=True 表示会输出思考段需剥离。
# ─────────────────────────────────────────────────────────────
ENGINE_REGISTRY = [
    {
        "name": "agnes",
        "base_url": "https://apihub.agnes-ai.com/v1",
        "key_env": "AGNES_API_KEY2",
        # agnes-2.0-flash 实测干净直出;agnes-2.5-pro 是思维链(会倒思考),留作兜底。
        "models": ["agnes-2.0-flash", "agnes-2.5-pro"],
        "thinking_models": {"agnes-2.5-pro"},
        "thinking": False,
    },
    {
        "name": "openrouter",
        "base_url": "https://openrouter.ai/api/v1",
        "key_env": "OPENROUTER_API_KEY",
        "models": ["google/gemma-4-31b-it", "minimax/minimax-m3:free",
                   "nvidia/nemotron-3-super-120b-a12b:free", "z-ai/glm-5.2"],
        "thinking_models": {"z-ai/glm-5.2"},  # glm 会先倒思考再正文
        "headers": {"HTTP-Referer": "https://prisir.local", "X-Title": "Prisir Philosopher"},
    },
    {
        "name": "ollama",
        "base_url": "https://ollama.com/v1",
        "key_env": "OLLAMA_API_KEY",
        "models": ["gemma4:31b", "nemotron-3-ultra"],
        "thinking": False,
    },
    {
        "name": "minimax",
        "base_url": "https://api.minimaxi.com/v1",
        "key_env": "MINIMAX_API_KEY",
        "models": ["MiniMax-M3"],
        "thinking": True,  # 全程 <think>…</think>
    },
    {
        "name": "moonshot",
        "base_url": "https://api.moonshot.cn/v1",
        "key_env": "MOONSHOT_API_KEY",
        "models": ["kimi-k3"],
        "thinking": True,  # 会把「用户要求我…」思考当正文倒出
    },
]

_MAX_WORKERS = 5
_CALL_TIMEOUT = 120   # 思维链引擎(agnes/kimi)推理慢,给足
_R1_TOKENS = 260      # 立场:够 120-150 字 + 缓冲(思维链引擎翻倍)
_R2_TOKENS = 380      # 辩论回应(思维链引擎翻倍)
_SYNTH_TOKENS = 600   # 综述
_PROBE_TOKENS = 8     # 探测:只验可调,不烧额度


# ─────────────────────────────────────────────────────────────
# 出字清洗:剥 <think>、剥「思考段」、兼容 reasoning 字段
# ─────────────────────────────────────────────────────────────
_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
# 思维链模型把「用户要求/分析请求/思考」开头的一大段当正文倒出,正文常落在末尾
# 或「孔子曰/斯多葛曰」等招牌开头之后。这里做保守剥离。
_LEADIN_RE = re.compile(
    r"^(?:用户要求|好的|我来|让我|分析|思考|首先|We need|Let me|The user|Okay)[^\n]*(?:\n[^\n]*){0,8}?\n+(?=[^\n]{0,6}(?:曰|认为|视角|曰[:：]))",
    re.M)


# 思考段常见开头(中英):这些行是模型在「想」,不是角色正文。
_THINK_LEAD = re.compile(
    r"^\s*(?:\*\*)?\s*(?:思考过程|思考|分析|我们需要|我先|让我|好的|用户要求|"
    r"Here'?s a thinking|Here'?s my|We need|Let me|Okay|First|The user|I need|"
    r"Thinking|Analysis|Step \d|#+\s*思考)[:：\s]",
    re.M | re.I)


def _clean_output(text: str, thinking: bool) -> str:
    """剥离思考段,返回角色正文。保守:宁可留一点也不误删正文。
    thinking=True 时优先截取「X曰」招牌起的内容。"""
    t = (text or "").strip()
    if not t:
        return ""
    # 1. 去 <think>…</think>
    t = _THINK_RE.sub("", t).strip()
    if not thinking:
        return t
    # 2. 优先:取最后一个「X曰」招牌起的内容(角色正文标志;模型思考在前、正文在后,
    #    取最后一个避免思考段里引用「子曰」被误当正文)
    matches = list(re.finditer(r"(?:^|\n)\s*\**\s*([^\n]{0,12}曰[:：])", t))
    if matches:
        t = t[matches[-1].start():].strip()
    else:
        # 3. 无招牌(综述等非角色场景):若开头是思考段,尝试切到最后一个空行后的正文
        if _THINK_LEAD.search(t[:200]):
            parts = re.split(r"\n\s*\n", t)
            if len(parts) > 1:
                t = parts[-1].strip()
    # 4. 去掉残留的思考标题行 + 空行
    lines = [ln for ln in t.splitlines()
             if ln.strip() and not _THINK_LEAD.match(ln)]
    return "\n".join(lines).strip() if lines else t


def _looks_like_thinking(text: str) -> bool:
    """清洗后仍像思考/分析过程(非正文)的判定。"""
    t = (text or "").strip()
    if not t:
        return True
    head = t[:400]
    if _THINK_LEAD.search(head):
        return True
    # 大量英文思考连接词 + 无明显中文 → 推理腔
    en_think = re.search(r"\b(Analyze|Step \d|Let's|First,? I|The user wants|"
                         r"I need to|thinking process|Constraints?:)\b", head, re.I)
    zh = len(re.findall(r"[一-鿿]", head))
    if en_think and zh < 30:
        return True
    return False


def _extract_text(data: dict) -> str:
    """从 chat.completions 响应取正文,兼容 content / reasoning_content / reasoning。"""
    ch = (data.get("choices") or [{}])[0]
    msg = ch.get("message", {}) or {}
    for k in ("content", "reasoning_content", "reasoning"):
        v = msg.get(k)
        if v and str(v).strip():
            return str(v)
    return ""


# ─────────────────────────────────────────────────────────────
# 单次调用(OpenAI 兼容)
# ─────────────────────────────────────────────────────────────
def _call(engine: dict, model: str, system: str, user: str, max_tokens: int,
          role_mode: bool = False) -> dict:
    """role_mode=True(学派立场/辩论):强制走招牌截取清洗,不论引擎 thinking 标注。"""
    key = os.environ.get(engine["key_env"], "")
    if not key:
        return {"ok": False, "err": f"缺环境变量 {engine['key_env']}"}
    body = json.dumps({
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": max_tokens,
    }).encode("utf-8")
    headers = {"Content-Type": "application/json", "Authorization": "Bearer " + key}
    headers.update(engine.get("headers") or {})
    req = urllib.request.Request(engine["base_url"].rstrip("/") + "/chat/completions",
                                 data=body, headers=headers)
    import time
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=_CALL_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        ms = int((time.time() - t0) * 1000)
        thinking = (engine.get("thinking", False)
                    or model in engine.get("thinking_models", set())
                    or role_mode)
        raw = _extract_text(data)
        text = _clean_output(raw, thinking)
        if not text:
            return {"ok": False, "err": "空响应(可能思维链未出正文,或额度/订阅受限)", "ms": ms}
        # 角色模式:清洗后仍像思考 → 视为失败(宁缺勿滥,别让模型碎碎念上屏)
        if role_mode and _looks_like_thinking(text):
            return {"ok": False, "err": "输出像思考过程未出角色正文", "ms": ms}
        return {"ok": True, "text": text, "ms": ms, "model": model, "engine": engine["name"]}
    except urllib.error.HTTPError as e:
        body_txt = ""
        try:
            body_txt = e.read().decode("utf-8", "replace")[:120]
        except Exception:  # noqa: BLE001
            pass
        return {"ok": False, "err": f"HTTP {e.code}: {body_txt}", "code": e.code}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "err": f"{type(e).__name__}: {e}"}


# ─────────────────────────────────────────────────────────────
# 探测:每个引擎选第一个真出字的模型;不可用引擎标记原因
# ─────────────────────────────────────────────────────────────
def probe_engines(probe_call: bool = True) -> list[dict]:
    """返回引擎状态表。probe_call=False 只查 key 是否在手(快,不发请求)。"""
    out = []
    for eng in ENGINE_REGISTRY:
        key = os.environ.get(eng["key_env"], "")
        entry = {"engine": eng["name"], "has_key": bool(key),
                 "usable": False, "model": None, "reason": ""}
        if not key:
            entry["reason"] = f"缺 {eng['key_env']}"
            out.append(entry)
            continue
        if not probe_call:
            # 只按 key 判定(乐观,标第一个模型;实际调用时再降级)
            entry["usable"] = True
            entry["model"] = eng["models"][0]
            entry["reason"] = "key 在手(未实调)"
            out.append(entry)
            continue
        # 实调探测:逐模型试到第一个真出字。思维链模型探测 token 要给足,
        # 否则前几个 token 全在 <think> 里,误判空响应。
        ok_model, last_err = None, ""
        is_thinking_eng = eng.get("thinking", False)
        probe_tokens = 400 if is_thinking_eng else _PROBE_TOKENS
        for m in eng["models"]:
            mt = 400 if (is_thinking_eng or m in eng.get("thinking_models", set())) else probe_tokens
            r = _call(eng, m, "Reply briefly: ok", "ping", mt)
            if r["ok"]:
                ok_model = m
                break
            last_err = r.get("err", "")
        if ok_model:
            entry.update(usable=True, model=ok_model, reason="实测可调")
        else:
            entry["reason"] = f"实测不可用({last_err[:60]})"
        out.append(entry)
    return out


def usable_engines(probe_call: bool = False) -> list[dict]:
    """可用引擎:[{engine(原始dict), model}]。默认快模式(按 key);调用层仍容错降级。
    排序:非思维链(干净直出)在前,思维链在后 — 角色正文更可能干净。"""
    out = []
    table = probe_engines(probe_call)
    by_name = {e["engine"]: e for e in table}
    for eng in ENGINE_REGISTRY:
        ent = by_name.get(eng["name"])
        if ent and ent["usable"]:
            thinking = eng.get("thinking", False) or ent["model"] in eng.get("thinking_models", set())
            out.append({"engine": eng, "model": ent["model"], "_thinking": thinking})
    out.sort(key=lambda s: s["_thinking"])
    return out


# ─────────────────────────────────────────────────────────────
# 学派面板(角色):与引擎解耦。每学派 = {id, name, 招牌, 视角提示词}
# 视角提示词要求:先点要害 → 一句可行日常建议 → 招牌开头 → 限字。
# ─────────────────────────────────────────────────────────────
def _sp(school_name: str, sign: str, viewpoint: str, limit: int = 150) -> str:
    return (f"你是{school_name}。{viewpoint}\n"
            f"评价规则:用简体中文;先一两句点出事件要害(从你的视角),"
            f"再给一句可落地的日常行事建议;不超过{limit}字;"
            f"以「{sign}」开头,不要分点、不要复述题目、不要自我介绍。")


PANELS = {
    "zhuzi": {
        "label": "诸子百家",
        "schools": [
            {"id": "kongzi", "name": "孔子(儒家)", "sign": "孔子曰",
             "view": "以儒家仁、礼、正名、己所不欲勿施于人、为政以德的视角看问题,重人伦秩序与教化。"},
            {"id": "laozi", "name": "老子(道家)", "sign": "老子曰",
             "view": "以道家道法自然、无为而治、柔弱胜刚强、祸福相倚的视角看问题,重顺应与克制过度作为。"},
            {"id": "mozi", "name": "墨子(墨家)", "sign": "墨子曰",
             "view": "以墨家兼爱、非攻、尚贤、节用、兴天下之利除天下之害的视角看问题,重实际功利与公平。"},
            {"id": "hanfei", "name": "韩非子(法家)", "sign": "韩非子曰",
             "view": "以法家法、术、势、人性自为、不务德而务法的视角看问题,重制度、赏罚与权力约束。"},
            {"id": "sunzi", "name": "孙子(兵家)", "sign": "孙子曰",
             "view": "以兵家知己知彼、上兵伐谋、因势利导、成本与胜算的视角看问题,重局势判断与策略。"},
        ],
    },
    "west": {
        "label": "西方哲学",
        "schools": [
            {"id": "stoic", "name": "斯多葛学派", "sign": "斯多葛曰",
             "view": "以控制二分法、德性即善、顺应自然、不为外物所动的视角看问题,重内心判断与可控之事。"},
            {"id": "util", "name": "功利主义", "sign": "功利主义者曰",
             "view": "以最大多数人的最大幸福、后果成本收益权衡的视角看问题,重净效用与受影响者整体。"},
            {"id": "kant", "name": "康德(义务论)", "sign": "康德曰",
             "view": "以绝对命令、人是目的而非手段、可普遍化准则的视角看问题,重义务与道德法则而非后果。"},
            {"id": "nietzsche", "name": "尼采", "sign": "尼采曰",
             "view": "以权力意志、重估一切价值、超越善恶、成为你自己的视角看问题,重生命力与创造。"},
            {"id": "aristotle", "name": "亚里士多德(德性论)", "sign": "亚里士多德曰",
             "view": "以德性、中道、实践智慧、目的因与良好生活的视角看问题,重品格养成与适度。"},
        ],
    },
    "detective": {
        "label": "案件推理",
        "schools": [
            {"id": "evidence", "name": "证据链分析", "sign": "证据分析曰",
             "view": "以现代刑侦证据链、可验证性、孤证不立、证据强度的视角看问题,重事实与证明。"},
            {"id": "motive", "name": "动机分析", "sign": "动机分析曰",
             "view": "以谁受益、动机与机会、行为背后的利益结构的视角看问题,重人性与利益驱动。"},
            {"id": "falsify", "name": "证伪与排除", "sign": "证伪分析曰",
             "view": "以波普尔式证伪、寻找反例、排除不可能、警惕确认偏误的视角看问题,重检验与反驳。"},
            {"id": "risk", "name": "风险评估", "sign": "风险评估曰",
             "view": "以概率、影响面、最坏情形、可预防性与应对预案的视角看问题,重风险敞口与缓释。"},
        ],
    },
}

DEFAULT_PANEL = "zhuzi"


def panel_labels() -> list[dict]:
    return [{"key": k, "label": v["label"], "count": len(v["schools"])}
            for k, v in PANELS.items()]


def get_schools(panel: str) -> list[dict]:
    p = PANELS.get(panel) or PANELS[DEFAULT_PANEL]
    return p["schools"]


def build_system_prompt(school: dict) -> str:
    return _sp(school["name"], school["sign"], school["view"])


# ─────────────────────────────────────────────────────────────
# 调度器:把 N 学派散布到 M 可用引擎(优雅降级),再跑三轮
# ─────────────────────────────────────────────────────────────
def _assign_engines(schools: list[dict], engines: list[dict]) -> list[dict]:
    """每个学派分配一个 (engine dict, model)。engines 是 usable_engines 的
    [{engine, model}]。M>=N 一学派一引擎;M<N 轮转复用。"""
    assigned = []
    n_eng = len(engines)
    for i, sch in enumerate(schools):
        slot = engines[i % n_eng]
        assigned.append({"school": sch, "engine": slot["engine"], "model": slot["model"]})
    return assigned


def _mode_note(n_schools: int, n_eng: int) -> str:
    if n_eng == 0:
        return "无可用引擎"
    if n_eng >= n_schools:
        return f"真合议:{n_schools} 学派 × {n_eng} 引擎,一学派一模型,声音最独立"
    if n_eng > 1:
        return f"半合议:{n_schools} 学派散布到 {n_eng} 引擎,部分同引擎分饰"
    return f"单引擎分饰:{n_schools} 学派共用 1 引擎({n_eng} 个可用),声音同源"


def run_philosopher(event: str, panel: str = DEFAULT_PANEL,
                    mode: str = "debate", max_schools: int = 5,
                    synthesize: bool = True) -> dict:
    """哲人模式主入口。返回 {ok, panel, mode_note, rounds, synthesis, errors, engine_table}。

    mode: "stance"(仅立场) / "debate"(立场→辩论→综述,默认)
    """
    event = (event or "").strip()
    if not event:
        return {"ok": False, "error": "空事件"}
    schools = get_schools(panel)[:max(1, min(max_schools, 8))]
    engines = usable_engines(probe_call=False)  # 快模式:按 key;调用层容错降级
    if not engines:
        return {"ok": False,
                "error": "无可用引擎:未检测到任何模型 key(环境变量)。"
                         "请配置 AGNES_API_KEY2 / OPENROUTER_API_KEY / OLLAMA_API_KEY / "
                         "MINIMAX_API_KEY / MOONSHOT_API_KEY 至少一个。"}

    assigned = _assign_engines(schools, engines)
    n_eng_used = len({a["engine"]["name"] for a in assigned})
    mode_note = _mode_note(len(schools), n_eng_used)
    result = {"ok": True, "panel": PANELS.get(panel, PANELS[DEFAULT_PANEL])["label"],
              "mode_note": mode_note, "event": event,
              "engines_used": sorted({a["engine"]["name"] for a in assigned}),
              "rounds": {}, "errors": []}

    # ── R1:各学派并行给立场 ──
    def _tokens_for(a, base):
        # 思维链引擎先烧思考 token,预算翻倍防正文没出就被截断
        eng = a["engine"]
        think = eng.get("thinking", False) or a["model"] in eng.get("thinking_models", set())
        return base * 2 if think else base
    r1 = {}
    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
        futs = {}
        for a in assigned:
            sp = build_system_prompt(a["school"])
            up = f"请评价这个事件:{event}"
            futs[ex.submit(_call, a["engine"], a["model"], sp, up, _tokens_for(a, _R1_TOKENS), True)] = a
        for fut in as_completed(futs):
            a = futs[fut]
            sch = a["school"]
            try:
                r = fut.result()
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "err": f"{type(e).__name__}: {e}"}
            if r["ok"]:
                r1[sch["id"]] = {"school": sch["name"], "sign": sch["sign"],
                                 "engine": a["engine"]["name"], "model": r.get("model"),
                                 "text": r["text"], "ms": r.get("ms")}
            else:
                result["errors"].append(f"{sch['name']}({a['engine']['name']}): {r.get('err')}")
    result["rounds"]["stance"] = r1
    if not r1:
        result["ok"] = False
        result["error"] = "全部学派调用失败:" + "; ".join(result["errors"][:3])
        return result

    # ── R2:辩论(互见立场后回应)──
    if mode == "debate" and len(r1) > 1:
        others_digest = "\n".join(
            f"- {v['school']}:{v['text']}" for v in r1.values())
        r2 = {}
        with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as ex:
            futs = {}
            for a in assigned:
                sch = a["school"]
                if sch["id"] not in r1:
                    continue
                sp = build_system_prompt(sch)
                up = (f"事件:{event}\n\n各学派已给出立场:\n{others_digest}\n\n"
                      f"请以你的视角,简要回应:你最认同哪一家、最反对哪一家,各一句理由;"
                      f"再补一句你立场的澄清或坚持。不超过150字,仍以「{sch['sign']}」开头。")
                futs[ex.submit(_call, a["engine"], a["model"], sp, up, _tokens_for(a, _R2_TOKENS), True)] = a
            for fut in as_completed(futs):
                a = futs[fut]
                sch = a["school"]
                try:
                    r = fut.result()
                except Exception as e:  # noqa: BLE001
                    r = {"ok": False, "err": f"{type(e).__name__}: {e}"}
                if r["ok"]:
                    r2[sch["id"]] = {"school": sch["name"], "sign": sch["sign"],
                                     "text": r["text"], "ms": r.get("ms")}
                else:
                    result["errors"].append(f"辩论-{sch['name']}: {r.get('err') or '未知错误'}")
        result["rounds"]["debate"] = r2

    # ── R3:综述 ──
    if synthesize and r1:
        all_text = "\n".join(f"【{v['school']}】{v['text']}" for v in r1.values())
        if result["rounds"].get("debate"):
            all_text += "\n\n辩论环节:\n" + "\n".join(
                f"【{v['school']}】{v['text']}" for v in result["rounds"]["debate"].values())
        # 综述无「X曰」招牌,思考段难剥 → 优先非思维链干净引擎;若输出仍像思考,换下一家重试。
        def _is_thinker(sl):
            eng = sl["engine"]
            return eng.get("thinking", False) or sl["model"] in eng.get("thinking_models", set())
        slots = sorted(engines, key=_is_thinker)  # 干净引擎在前
        sp = ("你是中立的评述者,综合多位思想者对同一事件的观点。"
              "直接输出三小节,严禁任何思考、分析、规划过程或英文,不要复述输入,不要解释你在做什么。"
              "用简体中文,格式严格如下:\n【共识】各家都认同的要点(一两句)\n"
              "【分歧】核心分歧在哪两三家之间、各一句\n"
              "【综合建议】给普通人的一句综合行动建议\n"
              "总长度不超过250字,平实。")
        up = f"事件:{event}\n\n各家观点:\n{all_text}"
        done = False
        last_err = ""
        for slot in slots:
            syn_engine, syn_model = slot["engine"], slot["model"]
            r = _call(syn_engine, syn_model, sp, up, _SYNTH_TOKENS)
            if r["ok"] and not _looks_like_thinking(r["text"]):
                result["synthesis"] = {"engine": syn_engine["name"], "text": r["text"]}
                done = True
                break
            if r["ok"]:
                last_err = f"{syn_engine['name']}输出像思考过程"
            else:
                last_err = f"{syn_engine['name']}: {r.get('err','')[:60]}"
        if not done and "synthesis" not in result:
            # 兜底:接受最后一次非空输出(宁脏勿缺),但标注
            if r.get("ok") and r.get("text"):
                result["synthesis"] = {"engine": syn_engine["name"],
                                       "text": r["text"], "note": "未经完全清洗"}
            else:
                result["errors"].append(f"综述失败: {last_err or '无可用干净输出'}")
    return result


# ─────────────────────────────────────────────────────────────
# 渲染:人类可读文本(供 CLI 工具回显 / 前端展示)
# ─────────────────────────────────────────────────────────────
def format_result(res: dict) -> str:
    if not res.get("ok"):
        return f"[哲人模式失败] {res.get('error', '未知错误')}"
    lines = [f"## 哲人模式 · {res['panel']}",
             f"_{res['mode_note']}_",
             f"事件:{res['event']}", ""]
    stance = res["rounds"].get("stance", {})
    if stance:
        lines.append("### 各家立场")
        for v in stance.values():
            tag = f"({v['engine']}·{v['model']}, {v.get('ms',0)}ms)"
            lines.append(f"\n**{v['school']}** {tag}\n{v['text']}")
    debate = res["rounds"].get("debate", {})
    if debate:
        lines.append("\n### 互相辩论")
        for v in debate.values():
            lines.append(f"\n**{v['school']}**\n{v['text']}")
    if res.get("synthesis"):
        lines.append(f"\n### 综述(共识/分歧/建议)\n{res['synthesis']['text']}")
    if res.get("errors"):
        err_str = "; ".join(str(e) for e in res["errors"][:3] if e)
        lines.append(f"\n_部分调用失败({len(res['errors'])}):{err_str}_")
    return "\n".join(lines)
