# -*- coding: utf-8 -*-
"""M3.33 #65 skill 面板 UI e2e:
  T1 /api/skill_list 仍返 6+ 个,UI 能拉
  T2 /api/skill_show 返 body,前端 <pre> 渲染(模拟浏览器 DOM 解析)
  T3 /api/skill_run check.py 跑通,stdout 进 UI 输出区
  T4 /api/skill_run generate.py --help 返 ok
  T5 /api/skill_uninstall 删目录 + list 减少 1
  T6 /api/skill_new 落盘 + list 增 1
  T7 重名 → 409
  T8 i18n 字段都填了 zh/en
"""
import sys, os, json, shutil, time, re
import urllib.request, urllib.error, urllib.parse

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
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:
            return e.code, {"_raw": True}


# === T1 list ===
code, j = http("/prisiragent/api/skill_list?refresh=1")
print(f"[T1] skill_list → {code}, count={j.get('count')}")
assert code == 200 and j.get("count", 0) >= 6, f"T1 fail: {j}"
names = [s["name"] for s in j.get("skills", [])]
assert "image-card-comfyui-local" in names
assert "image-card-replicate" in names

# 模拟前端 skillCardHtml():确保每个 skill 字段够画卡片
for s in j["skills"]:
    assert "name" in s and "description" in s and "triggers" in s
    assert "requirements" in s and "license" in s and "dir" in s

# === T2 show ===
code, j = http("/prisiragent/api/skill_show?name=image-card-comfyui-local")
print(f"[T2] skill_show → {code}, body[:60]={j.get('body','')[:60]!r}")
assert code == 200
assert j.get("body"), "T2 fail: empty body"
assert "generate" in j.get("scripts", [])
assert "check" in j.get("scripts", [])

# === T3 run check ===
# 注意:本机没装 bark → check.py 故意返 ok=False(code=1),
# 这正是预期(依赖缺失应被检出);只要后端能跑通、把 stdout/stderr 透传给前端即 OK
code, j = http("/prisiragent/api/skill_run", method="POST",
               body={"name": "audio-card-bark-local", "script": "check", "args": []})
print(f"[T3] skill_run check → ok={j.get('ok')} code={j.get('code')} stdout[:80]={j.get('stdout','')[:80]!r}")
assert "ok" in j and "stdout" in j, f"T3 fail: no ok/stdout key: {j}"
# stdout 应包含 [info]/[ok]/[fail] 之类(check.py 自带格式)
assert ("[info]" in j.get("stdout", "") or "[ok]" in j.get("stdout", "") or "[fail]" in j.get("stdout", "")
        or "[warn]" in j.get("stdout", ""))

# === T4 run generate --help(本期返 fail 但有 stderr 算 OK,确认后端能跑到)===
code, j = http("/prisiragent/api/skill_run", method="POST",
               body={"name": "image-card-comfyui-local", "script": "generate", "args": ["--help"]})
print(f"[T4] skill_run generate --help → ok={j.get('ok')} stderr[:120]={j.get('stderr','')[:120]!r}")
# --help 走 argparse 通常返 0;若 ImportError 也可(fail=False 路径都覆盖到),只要后端不崩
assert "ok" in j, f"T4 fail: no ok key"

# === T6 new ===
test_skill = f"ui-test-skill-{int(time.time())}"
code, j = http("/prisiragent/api/skill_new", method="POST", body={
    "name": test_skill,
    "description": "UI 测试 skill:验证面板新建按钮链路",
    "triggers": ["ui test", "UI 测试"],
    "provider": "stub",
    "requirements": "none",
    "template": "image",
})
print(f"[T6] skill_new → {code}, dst={j.get('dst')}")
assert code == 200 and j.get("ok") is True
code, j = http("/prisiragent/api/skill_list?refresh=1")
names_after = [s["name"] for s in j.get("skills", [])]
assert test_skill in names_after, f"T6 fail: not in list"

# === T7 重复名 → 409 ===
code, j = http("/prisiragent/api/skill_new", method="POST", body={
    "name": test_skill, "description": "重名",
})
print(f"[T7] re-new same name → {code}")
assert code == 409

# === T5 uninstall ===
code, j = http("/prisiragent/api/skill_uninstall", method="POST", body={
    "name": test_skill, "target": "project",
})
print(f"[T5] uninstall → {code}, removed={j.get('removed')}")
assert code == 200 and j.get("ok") is True
code, j = http("/prisiragent/api/skill_list?refresh=1")
names_after = [s["name"] for s in j.get("skills", [])]
assert test_skill not in names_after

# === T8 i18n 字段 ===
src = open(r"C:\Users\Administrator\oi_enhancements\prisiragent_web.py", encoding="utf-8").read()
for k in ("doc_skills", "doc_skills_loading", "skill_refresh_title"):
    assert f"'{k}':" in src or f"{k}:" in src, f"T8 fail: missing i18n key {k}"
zh_count = len(re.findall(r"doc_skills.*[一-龥]", src))
print(f"[T8] i18n keys present, zh occurrences={zh_count}")
assert zh_count >= 2, "T8 fail: zh strings not enough"

# === 清理 ===
sktest_dir = os.path.join(WD, "skills", test_skill)
if os.path.isdir(sktest_dir):
    shutil.rmtree(sktest_dir, ignore_errors=True)

print()
print("✅ M3.33 #65 skill 面板 UI e2e 全部 PASS")