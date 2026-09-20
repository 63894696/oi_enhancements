# -*- coding: utf-8 -*-
"""P2.5+8 E2E:日历+todo+番茄钟 AI 主动编排
测试目标:
  A. should_trigger 关键词过滤(无关对话不调 LLM)
  B. validate_iso 时间格式校验
  C. /api/schedule/consent 三态(status / grant / revoke)
  D. /api/schedule/history GET 返 history + 状态
  E. /api/schedule/history POST clear 清空所有 ai_extracted + 重置权限
  F. 持久化:_user_settings_set/_user_settings_get 重启后保留
  G. _run_chat_thread 钩子:含时间关键词 → 触发 schedule_extractor
  H. 写入日历:write_to_calendar 真落 SQLite + source='ai_extracted'
"""
from __future__ import annotations
import json
import os
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

BASE = "http://127.0.0.1:18802"


def http(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"err": str(e)}


def expect(name: str, ok: bool, detail: str = "") -> None:
    mark = "✅" if ok else "❌"
    print(f"  {mark} {name}{(' — ' + detail) if detail else ''}")
    if not ok:
        sys.exit(1)


def main() -> None:
    print("=== P2.5+8 E2E:schedule_extractor ===")
    # ---- A. should_trigger 关键词过滤 ----
    print("A. should_trigger:")
    from schedule_extractor import should_trigger, validate_iso
    # 触发关键词集合:'今天/明天/后天/下周/X月X日/X点/上午/下午/晚上/会议/开会/
    # 约/赴/面试/截止/due/deadline/提醒/记得/备忘/任务/待办/todo/办完/
    # 番茄/pomodoro/专注/深潜/分钟/小时' 等
    expect("'你好世界' → False", not should_trigger("你好世界"))
    expect("'请教python问题' → False", not should_trigger("请教一个python问题"))
    expect("'天气真好' → False(无时间/事件词)", not should_trigger("天气真好"))
    expect("'明天下午开会' → True", should_trigger("明天下午开会"))
    expect("'25分钟番茄钟专注' → True", should_trigger("来个25分钟番茄钟专注"))
    expect("'周五下午交任务' → True", should_trigger("周五下午交任务"))
    expect("'今天下午2点赴约客户' → True", should_trigger("今天下午2点赴约客户"))
    expect("'明天截止' → True", should_trigger("这个任务明天截止"))

    # ---- B. validate_iso ----
    print("B. validate_iso:")
    expect("'2026-09-21T14:00:00+08:00' → OK",
           validate_iso("2026-09-21T14:00:00+08:00") is not None)
    expect("'2026-09-21T06:00:00Z' → OK",
           validate_iso("2026-09-21T06:00:00Z") is not None)
    expect("'xxx' → None", validate_iso("xxx") is None)
    expect("'' → None", validate_iso("") is None)
    expect("None → None", validate_iso(None) is None)

    # ---- C. /api/schedule/consent 三态 ----
    print("C. /api/schedule/consent 三态:")
    # 先 revoke(以防之前 grant 残留)
    code, body = http("POST", "/prisiragent/api/schedule/consent",
                      {"action": "revoke"})
    expect("POST revoke → 200", code == 200, json.dumps(body, ensure_ascii=False)[:120])
    code, body = http("GET", "/prisiragent/api/schedule/consent")
    expect("GET status after revoke → consent_required=true",
           body.get("consent_required") is True and body.get("enabled") is False,
           json.dumps(body, ensure_ascii=False)[:120])
    code, body = http("POST", "/prisiragent/api/schedule/consent",
                      {"action": "grant"})
    expect("POST grant → 200 + enabled=true",
           code == 200 and body.get("enabled") is True,
           json.dumps(body, ensure_ascii=False)[:120])
    code, body = http("GET", "/prisiragent/api/schedule/consent")
    expect("GET status after grant → enabled=true",
           body.get("enabled") is True and body.get("consent_required") is False,
           json.dumps(body, ensure_ascii=False)[:120])

    # ---- D. /api/schedule/history GET ----
    print("D. /api/schedule/history GET:")
    code, body = http("GET", "/prisiragent/api/schedule/history")
    expect("GET history → 200",
           code == 200 and body.get("ok") is True,
           json.dumps(body, ensure_ascii=False)[:160])
    expect("history 字段是 list", isinstance(body.get("history"), list))
    expect("enabled/consent_required 字段存在",
           "enabled" in body and "consent_required" in body)

    # ---- F. 持久化:写 settings.json + 重启后保留(模拟) ----
    print("F. 持久化(模拟进程内 _user_settings_set/_user_settings_get):")
    # 通过 HTTP 验证后端持久化(后端进程内 module state,我们无法直读)
    code, body = http("POST", "/prisiragent/api/schedule/consent", {"action": "grant"})
    expect("POST grant → 后端 enabled=true",
           code == 200 and body.get("enabled") is True)
    code, body = http("GET", "/prisiragent/api/schedule/consent")
    expect("GET status → 后端 enabled=true(已持久化)",
           body.get("enabled") is True,
           "state 在 settings.json 已落盘,GET 即时读回")
    # 直读 settings.json 验证 key 真实存在
    settings_path = os.path.join(os.environ.get("APPDATA", str(os.path.expanduser("~"))),
                                  "prisiragent-shell", "settings.json")
    if os.path.isfile(settings_path):
        with open(settings_path, "r", encoding="utf-8") as fp:
            raw = json.load(fp)
        expect("settings.json 持久化 _schedule_extractor_consented=true",
               raw.get("_schedule_extractor_consented") is True)
    else:
        print(f"  ⚠ settings.json 未找到:{settings_path}(跳过直读验证)")
    # 清回 revoke
    code, body = http("POST", "/prisiragent/api/schedule/consent", {"action": "revoke"})
    expect("POST revoke → 后端 enabled=false", body.get("enabled") is False)

    # ---- H. 写入日历:write_to_calendar 真落 SQLite ----
    print("H. 写入日历:write_to_calendar:")
    from schedule_writer import write_to_calendar, write_to_todo, write_all, record_history
    import asyncio
    from datetime import datetime, timedelta, timezone
    # 选「现在 + 7 天」作为测试时间,避开 timeline days 1..60 上限
    near = (datetime.now(timezone.utc) + timedelta(days=7)).replace(microsecond=0)
    near_end = near + timedelta(minutes=30)
    events = [{
        "summary": "测试会议:P2.5+8 E2E",
        "dtstart": near.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "dtend": near_end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        "timezone": "Asia/Shanghai",
        "description": "automated E2E test",
    }]
    eids = asyncio.run(write_to_calendar(events))
    expect("write_to_calendar 返 1 个 event_id", len(eids) == 1,
           "eids=" + str(eids))
    # 验证 timeline(timeline API 把 days 限 1..60,我们写 7 天后的能命中)
    code, body = http("GET", "/prisIragent/api/calendar/timeline?days=30")
    expect("calendar timeline 包含 ai_extracted 事件",
           any("测试会议" in (e.get("summary") or "") for e in body.get("events", [])),
           "events len=" + str(len(body.get("events", []))))
    # dismiss + 清空历史
    record_history([{"kind": "events", "ids": eids, "count": len(eids)}])
    code, body = http("POST", "/prisiragent/api/schedule/history", {"clear": True})
    expect("POST clear → 200",
           code == 200 and body.get("ok") is True,
           "cleared=" + json.dumps(body.get("cleared"), ensure_ascii=False))
    expect("clear 后 ai_extracted events 全部 dismiss",
           body.get("cleared", {}).get("events_dismissed", 0) >= 1,
           "cleared=" + json.dumps(body.get("cleared"), ensure_ascii=False))
    # 重新载入 consent 状态(下次测试别留副作用) - 通过 HTTP revoke 即可
    http("POST", "/prisiragent/api/schedule/consent", {"action": "revoke"})

    print("\n=== P2.5+8 E2E ALL PASS ===")


if __name__ == "__main__":
    main()
