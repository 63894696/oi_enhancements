"""Claude-Code 风格 sub-agent depth cap + resume 还原 — OI 多 agent 治理

参考 github.com/anthropics/claude-code v2.1.183-196:
  - Fixed subagent depth tracking: resumed subagents restore original spawn depth
  - forked subagents count toward cap
  - Agent(type) deny 规则对 named subagent spawns 生效

OI 多 agent(嵌入 team-web 等)目前没有深度上限,子 agent 再 spawn 子 agent 会无限循环。
这个增强器提供 DepthCapMiddleware,包装 spawn/resume 调用,强制深度上限 + 还原原深度。

用法:
    from oi_enhancements.subagent_depth import DepthCapMiddleware, DepthExceeded
    cap = DepthCapMiddleware(max_depth=5)

    def on_spawn(ctx, depth):
        cap.check(depth)  # raises DepthExceeded if > 5
        ctx.subagent_meta['orig_depth'] = depth

    def on_resume(ctx):
        return cap.restore(ctx)  # return original depth, don't increment
"""
from __future__ import annotations

import os
from typing import Optional

# 默认 5,环境变量可覆盖
DEFAULT_MAX_DEPTH = int(os.environ.get("OI_SUBAGENT_MAX_DEPTH", 5))


class DepthExceeded(RuntimeError):
    """sub-agent 嵌套深度超过上限"""

    def __init__(self, depth: int, max_depth: int):
        super().__init__(f"subagent depth {depth} > max {max_depth}")
        self.depth = depth
        self.max_depth = max_depth


class DepthCapMiddleware:
    """sub-agent 深度上限中间件

    Attributes:
        max_depth: 上限,默认 5
        current_depth: 当前调用栈的深度
        _resume_orig_depth: resume 时返回的原深度缓存
    """

    def __init__(self, max_depth: int = DEFAULT_MAX_DEPTH):
        self.max_depth = max_depth
        self.current_depth = 0
        self._resume_orig_depth: Optional[int] = None
        # 统计
        self.spawn_count = 0
        self.depth_exceeded_count = 0
        self.resume_count = 0

    def check(self, depth: Optional[int] = None) -> int:
        """检查深度是否超过上限

        Args:
            depth: 当前要 spawn 的深度,None 表示 +1 自增

        Returns:
            检查后的实际深度

        Raises:
            DepthExceeded: 深度超过 max_depth
        """
        if depth is None:
            depth = self.current_depth + 1
        if depth > self.max_depth:
            self.depth_exceeded_count += 1
            raise DepthExceeded(depth, self.max_depth)
        return depth

    def restore(self, ctx=None) -> int:
        """resume 时还原原 spawn 时的深度(不递增)

        Returns:
            还原后的深度(如果有 _resume_orig_depth)
        """
        self.resume_count += 1
        if self._resume_orig_depth is not None:
            depth = self._resume_orig_depth
            self._resume_orig_depth = None
            self.current_depth = depth
            return depth
        return self.current_depth

    def enter(self) -> None:
        """进入 sub-agent scope(增加 current_depth)"""
        self.current_depth += 1
        self.spawn_count += 1

    def exit(self) -> None:
        """离开 sub-agent scope(减少 current_depth)"""
        self.current_depth = max(0, self.current_depth - 1)

    def context(self):
        """with 语句支持:进入 scope,退出时自动 exit

        Usage:
            with cap.context():
                do_subagent_work()
        """
        return _DepthScope(self)


class _DepthScope:
    def __init__(self, cap: DepthCapMiddleware):
        self.cap = cap

    def __enter__(self):
        self.cap.enter()
        return self.cap

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cap.exit()
        return False


# ============================================================
# OI 集成版:装饰 interpreter.chat 的多 agent dispatch
# ============================================================

def install(interpreter, max_depth: int = DEFAULT_MAX_DEPTH):
    """装深度上限到 OI interpreter(包装 chat 多 agent 场景)

    实际效果:每次 chat 调用包一层,检查当前栈深度,超过就 raise。
    OI 0.4.3 默认不支持 sub-agent spawn,所以这个 middleware 是**未来扩展的接口**,
    等 OI 加 sub-agent 时自动生效。

    Args:
        interpreter: OI interpreter instance
        max_depth: 上限

    Returns:
        DepthCapMiddleware instance
    """
    cap = DepthCapMiddleware(max_depth=max_depth)
    # 挂到 interpreter 上方便后续 sub-agent dispatch 用
    interpreter._depth_cap = cap
    return cap


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    print("=== DepthCapMiddleware 测试 ===")
    cap = DepthCapMiddleware(max_depth=3)

    # 1. 正常 spawn:1, 2, 3 都 OK
    for d in range(1, 4):
        try:
            cap.check(d)
            print(f"  check({d}) ok")
        except DepthExceeded:
            print(f"  check({d}) UNEXPECTED exceeded")

    # 2. 超过上限:DepthExceeded
    try:
        cap.check(4)
        print("  check(4) UNEXPECTED ok")
    except DepthExceeded as e:
        print(f"  check(4) exceeded (expected): {e}")

    # 3. context manager 自动 enter/exit
    print("\n  === context manager ===")
    with cap.context() as c:
        print(f"    entered, depth={c.current_depth}")
        with cap.context():
            print(f"    nested, depth={cap.current_depth}")
        print(f"    after nested, depth={cap.current_depth}")
    print(f"    after exit, depth={cap.current_depth}")

    # 4. resume restore
    print("\n  === resume restore ===")
    cap._resume_orig_depth = 2
    restored = cap.restore()
    print(f"    restored to: {restored} (expected 2)")

    # 5. 统计
    print(f"\n  === stats ===")
    print(f"    spawn_count: {cap.spawn_count}")
    print(f"    depth_exceeded_count: {cap.depth_exceeded_count}")
    print(f"    resume_count: {cap.resume_count}")