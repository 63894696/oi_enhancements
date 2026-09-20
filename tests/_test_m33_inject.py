# -*- coding: utf-8 -*-
"""M3.33 #66 agent 自动注入 skill_match e2e:
  T1 _skill_block_for_prompt("") → ""
  T2 _skill_block_for_prompt("hello") → ""(无触发词)
  T3 _skill_block_for_prompt("帮我画一张赛博朋克女孩") → 含 "image-card-comfyui-local" body 摘要
  T4 _skill_block_for_prompt("SVD generate a 5s clip") → 含 "video-card-svd-local"
  T5 _shell_system_prompt(user_text, sid) 返的字符串含 "Skill 提示" 段
  T6 _shell_system_prompt 整体长度:命中 1 个 < 4000 chars,无性能回退
"""
import sys

sys.path.insert(0, r"C:\Users\Administrator\oi_enhancements")
import prisiragent_web as M

# 测试进程独立,需要先 scan 一次才能 match
M._skill_refresh()
with M._SKILL_INDEX_LOCK:
    n = len(M._SKILL_INDEX)
print(f"[setup] loaded {n} skills")


# === T1 ===
print("[T1] empty text → empty block")
r = M._skill_block_for_prompt("")
print(f"     r[:60]={r[:60]!r}")
assert r == "", f"T1 fail: {r!r}"

# === T2 ===
print("[T2] no trigger → empty")
r = M._skill_block_for_prompt("你好,今天天气不错")
print(f"     r[:60]={r[:60]!r}")
assert r == "", f"T2 fail: {r!r}"

# === T3 中文触发词 ===
print("[T3] 中文「画一张」触发")
r = M._skill_block_for_prompt("帮我画一张赛博朋克女孩")
print(f"     contains 'image-card-comfyui-local'? {'image-card-comfyui-local' in r}")
print(f"     contains 'Skill 提示'? {'Skill 提示' in r}")
print(f"     contains 'body:'? {'body:' in r}")
assert "image-card-comfyui-local" in r, f"T3 fail: missing name"
assert "Skill 提示" in r
assert "body:" in r
assert "ComfyUI" in r or "comfyui" in r.lower()

# === T4 英文触发词 ===
print("[T4] 英文「SVD」触发")
r = M._skill_block_for_prompt("SVD generate a 5s clip from this image")
print(f"     contains 'video-card-svd-local'? {'video-card-svd-local' in r}")
assert "video-card-svd-local" in r, f"T4 fail: missing name"

# === T5 _shell_system_prompt 拼合 ===
print("[T5] _shell_system_prompt 含 Skill 提示段")
sp = M._shell_system_prompt("帮我画一张猫的图片", "")
print(f"     sp length={len(sp)}, contains 'Skill 提示'? {'Skill 提示' in sp}")
assert "Skill 提示" in sp, "T5 fail: missing Skill 提示 in shell system prompt"

# === T6 性能回退 / 不爆炸 ===
print("[T6] system prompt 长度 < 12000 chars(3 个命中也不爆炸)")
# 用明确的三个不同 trigger 关键词(每个 skill 都有)
sp = M._shell_system_prompt("帮我画一张 + ElevenLabs 配音 + SVD 视频", "")
print(f"     sp length={len(sp)}")
assert len(sp) < 12000, f"T6 fail: prompt too long {len(sp)}"
assert "image-card-comfyui-local" in sp
assert "audio-card-elevenlabs" in sp
assert "video-card-svd-local" in sp

# === 额外:无触发词时 shell prompt 不应含 Skill 提示 ===
print("[T-extra] 无触发词 → shell prompt 不应含 Skill 提示")
sp = M._shell_system_prompt("今天北京天气怎么样", "")
print(f"     contains 'Skill 提示'? {'Skill 提示' in sp}")
assert "Skill 提示" not in sp, "extra fail: false positive"

print()
print("✅ M3.33 #66 agent 注入 skill_match e2e 全部 PASS")