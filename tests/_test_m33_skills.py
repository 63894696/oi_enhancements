# -*- coding: utf-8 -*-
"""M3.33 skill 系统 e2e:
  T1 启动扫描加载 6 个示例 skill
  T2 SKILL.md frontmatter 解析(name/description/triggers)
  T3 /api/skill_list 返 JSON,含 name/description/triggers/license/dir
  T4 /api/skill_show?name=... 返 body + scripts list
  T5 /api/skill_match 中文触发词命中
  T6 /api/skill_match 英文触发词命中
  T7 触发词模糊(消息里有 skill 关键词)
  T8 /api/skill_new 对话式生成(填字段 → 落盘 → refresh)
  T9 生成的 skill 自动出现在 /api/skill_list
  T10 /api/skill_uninstall + 重扫后消失
  T11 skill_run 跑通(check.py 应当 exit 0 / 有输出)
  T12 不存在的 skill → 404
  T13 重新装同名 skill → 409(已存在)
"""
import sys
import os
import json
import shutil
import tempfile
import urllib.request
import urllib.error

sys.path.insert(0, r"C:\Users\Administrator\oi_enhancements")
import prisiragent_web as M

WD = M._WORKDIR.get("path", "") if hasattr(M._WORKDIR, "get") else (M._WORKDIR or "")
PORT = 18802


def http(path, method="GET", body=None):
    url = f"http://127.0.0.1:{PORT}{path}"
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"_raw": True}


# === T1 ===
M._skill_refresh()
with M._SKILL_INDEX_LOCK:
    n = len(M._SKILL_INDEX)
print(f"[T1] loaded {n} skills")
assert n >= 6, f"T1 fail: only {n}"

# === T3 ===
code, j = http("/prisiragent/api/skill_list")
print(f"[T3] skill_list → {code}, count={j.get('count')}")
assert code == 200 and j.get("count") >= 6
names = [s["name"] for s in j.get("skills", [])]
print(f"     names: {names}")
assert "image-card-comfyui-local" in names
assert "image-card-replicate" in names
assert "image-card-openai" in names
assert "audio-card-bark-local" in names
assert "audio-card-elevenlabs" in names
assert "video-card-svd-local" in names

# === T2 ===
sk = M._skill_parse_skill_md(os.path.join(WD, "skills", "image-card-comfyui-local", "SKILL.md"))
print(f"[T2] SKILL.md parsed: name={sk['name']} triggers[:3]={sk['triggers'][:3]} body[:40]={sk['body'][:40]!r}")
assert sk["name"] == "image-card-comfyui-local"
assert "抽卡" in sk["triggers"]

# === T4 ===
code, j = http("/prisiragent/api/skill_show?name=image-card-comfyui-local")
print(f"[T4] skill_show → {code}, scripts={j.get('scripts')}, body[:60]={j.get('body','')[:60]!r}")
assert code == 200
assert "generate" in j.get("scripts", []), f"T4 fail: scripts={j.get('scripts')}"
assert "check" in j.get("scripts", []), f"T4 fail: scripts={j.get('scripts')}"
assert j.get("references") == [], "T4 fail: should be no references"

# === T5 中文 ===
code, j = http("/prisiragent/api/skill_match?text=" + urllib.parse.quote("帮我画一张赛博朋克女孩"))
print(f"[T5] match zh → {j.get('matches')}")
assert "image-card-comfyui-local" in j.get("matches", [])

# === T6 英文 ===
code, j = http("/prisiragent/api/skill_match?text=" + urllib.parse.quote("ElevenLabs voice clone for narration"))
print(f"[T6] match en → {j.get('matches')}")
assert "audio-card-elevenlabs" in j.get("matches", [])

# === T7 ===
code, j = http("/prisiragent/api/skill_match?text=" + urllib.parse.quote("SVD generate a 5s clip"))
print(f"[T7] match SVD → {j.get('matches')}")
assert "video-card-svd-local" in j.get("matches", [])

# === T8 skill_new(落盘到 workdir/skills/)===
import time
test_skill = f"test-skill-{int(time.time())}"
code, j = http("/prisiragent/api/skill_new", method="POST", body={
    "name": test_skill,
    "description": "测试 skill:e2e 临时创建",
    "triggers": ["test trigger", "e2e 测试", "测试"],
    "provider": "comfyui-local",
    "requirements": "none",
    "template": "image",
})
print(f"[T8] skill_new → {code}, dst={j.get('dst')}")
assert code == 200 and j.get("ok") is True
dst = j.get("dst", "")
assert os.path.isdir(dst), f"T8 fail: dst not dir {dst}"
assert os.path.isfile(os.path.join(dst, "SKILL.md"))
assert os.path.isfile(os.path.join(dst, "scripts", "image_generate.py"))
assert os.path.isfile(os.path.join(dst, "scripts", "check.py"))
assert os.path.isfile(os.path.join(dst, "README.md"))

# === T9 ===
code, j = http("/prisiragent/api/skill_list?refresh=1")
names_after = [s["name"] for s in j.get("skills", [])]
print(f"[T9] after new: count={j.get('count')}, contains {test_skill}? {test_skill in names_after}")
assert test_skill in names_after

# === T11 skill_run 跑新 skill 的 check.py ===
code, j = http("/prisiragent/api/skill_run", method="POST", body={
    "name": test_skill, "script": "check", "args": [],
})
print(f"[T11] skill_run check → ok={j.get('ok')} code={j.get('code')} stdout[:100]={j.get('stdout','')[:100]!r}")
assert j.get("ok") is True, f"T11 fail: {j}"
assert "provider" in j.get("stdout", "").lower()

# === T12 不存在的 skill ===
code, j = http("/prisiragent/api/skill_show?name=does-not-exist")
print(f"[T12] show missing → {code}")
assert code == 404

# === T13 重新装同名 skill → 409 ===
# 先把生成的 skill uninstall 再 install(走 copy 路径)
# 这里 skill_install 走 copy tree,不接受我们生成的位置(已在 dst),跳过 13,改测重名 409
code, j = http("/prisiragent/api/skill_new", method="POST", body={
    "name": test_skill,  # 同名
    "description": "重名测试",
})
print(f"[T13] re-new same name → {code}, err={j.get('err','')}")
assert code == 409

# === T10 uninstall ===
code, j = http("/prisiragent/api/skill_uninstall", method="POST", body={
    "name": test_skill, "target": "project",
})
print(f"[T10] uninstall → {code}, removed={j.get('removed')}")
assert code == 200 and j.get("ok") is True
code, j = http("/prisiragent/api/skill_list?refresh=1")
names_after = [s["name"] for s in j.get("skills", [])]
assert test_skill not in names_after, f"T10 fail: still there"
print(f"     after uninstall, count={j.get('count')}")

# === 清理:保险 ===
sktest_dir = os.path.join(WD, "skills", test_skill)
if os.path.isdir(sktest_dir):
    shutil.rmtree(sktest_dir, ignore_errors=True)

print()
print("✅ M3.33 skill e2e 全部 PASS")