"""tests/oiagent_context_test.py — 壳上下文窗口管理单元测试(档位1+2)。

覆盖 oiagent_context.py:
  - estimate_tokens: 中文/英文/混排/空串
  - context_window: 已知模型/未知模型/带 litellm 前缀
  - usage_for: 用量/ratio/near_full/mask/advise
  - mask_old_tool_outputs: 保留最近 N 条 tool,遮蔽更早长输出,短输出不动,
    user/assistant 不动,不改传入 list(返回副本)

跑法: python tests/oiagent_context_test.py  →  打印 PASS/FAIL
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import oiagent_context as oc  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

_fails = []


def check(name, cond):
    print(("✓ " if cond else "✗ ") + name)
    if not cond:
        _fails.append(name)


def test_estimate_tokens():
    check("空串=0", oc.estimate_tokens("") == 0)
    check("None=0", oc.estimate_tokens(None) == 0)
    # 纯中文: 10 个 CJK 字 ≈ 10 token
    check("中文10字≈10", oc.estimate_tokens("你好世界这是一句话啊") == 10)
    # 纯英文: 40 字符 ≈ 10 token
    check("英文40字符≈10", oc.estimate_tokens("a" * 40) == 10)
    # 混排: 5 CJK + 8 other = 5 + 2 = 7
    check("混排 5中+8英=7", oc.estimate_tokens("你好世界啊abcdefgh") == 7)


def test_context_window():
    check("claude-opus → 200000 known", oc.context_window("claude-opus-4") ==
          {"window": 200000, "known": True})
    check("带前缀 openai/qwen3-coder-plus → 131072",
          oc.context_window("openai/qwen3-coder-plus-2025-09-23")["window"] == 131072)
    check("dashscope/qwen3-coder-plus → known",
          oc.context_window("dashscope/qwen3-coder-plus-2025-09-23")["known"] is True)
    unk = oc.context_window("totally-unknown-model-xyz")
    check("未知模型 → DEFAULT 8000 unknown",
          unk == {"window": oc.DEFAULT_WINDOW, "known": False})
    check("None 模型 → DEFAULT unknown",
          oc.context_window(None) == {"window": oc.DEFAULT_WINDOW, "known": False})
    check("minimax-m3 → 245760", oc.context_window("minimax-m3")["window"] == 245760)


def test_usage_for():
    # 构造一组小消息,模型用 moonshot-v1-8k (window=8000)
    msgs = [{"role": "user", "content": "你好"}, {"role": "assistant", "content": "你好,有什么可以帮你?"}]
    u = oc.usage_for(msgs, "moonshot-v1-8k")
    check("usage 返回字段齐全",
          all(k in u for k in ("used", "window", "known", "ratio", "near_full", "mask", "advise")))
    check("小用量 near_full=False mask=False", (not u["near_full"]) and (not u["mask"]))
    check("known 模型 advise=None", u["advise"] is None)

    # 未知模型 → advise 非空
    u2 = oc.usage_for(msgs, "unknown-model")
    check("未知模型 advise 非空", bool(u2["advise"]))

    # 超大用量触发 near_full + mask(用一个 8000 窗口模型塞爆)
    big = [{"role": "user", "content": "字" * 7000}]  # 7000 CJK ≈ 7000 tok
    u3 = oc.usage_for(big, "moonshot-v1-8k")
    check("7000/8000 → near_full=True", u3["near_full"] is True)
    check("7000/8000 → mask=True", u3["mask"] is True)

    # 阈值边界: ratio >= MASK_RATIO(0.70) 即 mask
    border = [{"role": "user", "content": "字" * 5600}]  # 5600/8000 = 0.70
    u4 = oc.usage_for(border, "moonshot-v1-8k")
    check("0.70 边界 → mask=True", u4["mask"] is True)


def test_mask_old_tool_outputs():
    # 造 10 条 tool 输出(每条 300 字符,>= 200 阈值会被遮蔽)+ 若干 user/assistant
    msgs = []
    for i in range(10):
        msgs.append({"role": "user", "content": f"问题{i}"})
        msgs.append({"role": "tool", "content": f"工具{i}输出" + "x" * 300})
        msgs.append({"role": "assistant", "content": f"回答{i}"})

    masked = oc.mask_old_tool_outputs(msgs, keep_recent=3)
    # 不改传入 list
    check("不就地改传入 list", msgs[1]["content"].startswith("工具0输出"))

    tool_masked = [m for m in masked if m["role"] == "tool"]
    orig_tool = [m for m in msgs if m["role"] == "tool"]
    check("tool 总数不变(10)", len(tool_masked) == 10)
    # 前 7 条(10-3)被遮蔽,后 3 条保留
    n_masked = sum(1 for m in tool_masked if "已遮蔽" in m["content"])
    check("遮蔽 7 条(保留最近3)", n_masked == 7)
    # 最后 3 条 tool 内容保留原文
    check("最近3条 tool 保留原文", tool_masked[-1]["content"] == orig_tool[-1]["content"]
          and tool_masked[-2]["content"] == orig_tool[-2]["content"]
          and tool_masked[-3]["content"] == orig_tool[-3]["content"])
    # user/assistant 全保留
    ua_masked = [m for m in masked if m["role"] in ("user", "assistant")]
    ua_orig = [m for m in msgs if m["role"] in ("user", "assistant")]
    check("user/assistant 全保留未动",
          all(a["content"] == b["content"] for a, b in zip(ua_masked, ua_orig)))
    # 遮蔽占位符留原长度
    m0 = [m for m in masked if m["role"] == "tool"][0]
    check("占位符含原长度", f"原 {len(orig_tool[0]['content'])} 字符" in m0["content"])

    # 短 tool 输出(<200)不遮蔽
    short = [{"role": "tool", "content": "ok"}, {"role": "tool", "content": "done"}]
    short_masked = oc.mask_old_tool_outputs(short, keep_recent=0)
    check("短 tool 输出不遮蔽", all("已遮蔽" not in m["content"] for m in short_masked))

    # 空/None 输入
    check("空 list → 空", oc.mask_old_tool_outputs([]) == [])
    check("None → []", oc.mask_old_tool_outputs(None) == [])


def test_mask_integration_usage_threshold():
    """集成: 超 MASK_RATIO 的历史经 mask 后,估算用量应明显下降;自适应收紧回到阈值下。"""
    # 用一个 8000 窗口模型,塞入多条长 tool 输出 + 少量对话
    msgs = []
    for i in range(8):
        msgs.append({"role": "user", "content": f"查一下文件{i}"})
        msgs.append({"role": "tool", "content": ("结果" + "数据" * 430)})  # ~862 CJK ≈ 862 tok
        msgs.append({"role": "assistant", "content": f"看到了{i}"})
    before = oc.usage_for(msgs, "moonshot-v1-8k")
    check("塞入后超阈值 mask=True", before["mask"] is True)

    # 固定 keep_recent=6(不传 model): 只遮蔽 2 条,用量下降但可能仍在阈值上
    fixed = oc.mask_old_tool_outputs(msgs)  # 默认 keep=6, 无 model
    kept_fixed = [m for m in fixed if m["role"] == "tool" and "已遮蔽" not in m["content"]]
    check(f"不传 model 保留最近 {oc.KEEP_RECENT_TOOL} 条 tool", len(kept_fixed) == oc.KEEP_RECENT_TOOL)

    # 传 model 自适应收紧: 应遮到 ratio < MASK_RATIO(或 keep=0)
    after_msgs = oc.mask_old_tool_outputs(msgs, model="moonshot-v1-8k")
    after = oc.usage_for(after_msgs, "moonshot-v1-8k")
    check("自适应 mask 后用量下降", after["used"] < before["used"])
    check("自适应 mask 后回落到阈值下", after["ratio"] < oc.MASK_RATIO)
    # 数据库原文不受影响
    check("DB 原文不受影响", msgs[1]["content"].startswith("结果数据"))


def main():
    print("=== oiagent_context 单元测试 ===\n")
    test_estimate_tokens()
    print()
    test_context_window()
    print()
    test_usage_for()
    print()
    test_mask_old_tool_outputs()
    print()
    test_mask_integration_usage_threshold()
    print("\n=== 判定 ===")
    if _fails:
        print(f"FAIL: {len(_fails)} 项未过 -> {_fails}")
        return 1
    print("PASS: 全部通过。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
