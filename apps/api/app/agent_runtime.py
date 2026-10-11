"""轻量 Agent 运行时（七期 Phase 3，文档 6.2 七期 / 6.5 第 8 条）。

从七期两个真实功能点（岗位追踪自主 / 面试对话自主）提炼的最小编排共性：
- 步骤（step / astep）：带名字的可失败执行单元；异常不外抛、只记为失败步骤
  （「失败静默降级」的统一落点），返回值作为 detail 进入轨迹；
- 上下文（ctx）：节点间共享的黑板 dict，调用方自行约定键；
- 轨迹（trajectory）：每步 {step, ok, detail, ms}；summary() 一次性导出，
  定时任务把轨迹写进运行结果、面试追问把轨迹写进轮次 meta（可观测性）；
- 上限与中止：max_steps 硬上限防失控循环；abort() 置原因后剩余步骤跳过；
- 工具注册（ToolRegistry）：名字 -> 可调用的登记表；七期岗位侧把
  boss-agent-cli 的三个只读原语（status / search / detail）集中注册于此，
  「只读合规面」在一处可见可查，不散落在管线各步。

刻意不做的事（避免凭空抽象，文档 6.2 七期硬约束）：不做 DAG / 条件路由引擎、
不做并行调度、不做跨请求持久会话——七期两个功能点都是「线性节点 + 有界循环」
形态，用不上更重的抽象。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class StepRecord:
    """一步的执行记录（轨迹元素）。"""

    step: str
    ok: bool
    detail: str = ""
    ms: int = 0


class AgentRun:
    """一次有界编排运行：步骤 + 上下文 + 轨迹 + 中止。"""

    def __init__(self, name: str, ctx: dict[str, Any] | None = None, max_steps: int = 64) -> None:
        self.name = name
        self.ctx: dict[str, Any] = ctx if ctx is not None else {}
        self.max_steps = max_steps
        self.trajectory: list[StepRecord] = []
        self.aborted = ""  # 非空即整轮中止原因（静默降级口径），剩余步骤跳过

    @property
    def steps_used(self) -> int:
        return len(self.trajectory)

    def abort(self, reason: str) -> None:
        """中止整轮（首个原因生效）；只置标志不抛异常，由调用方决定收尾。"""
        if not self.aborted:
            self.aborted = str(reason)[:120]

    def step(self, name: str, fn: Callable[["AgentRun"], Any]) -> StepRecord | None:
        """同步步骤：fn(run) 返回值转字符串入轨迹；异常一律捕获记为失败不外抛。

        已中止或步骤数达上限时直接跳过（返回 None）。
        """
        if self.aborted or self.steps_used >= self.max_steps:
            if not self.aborted:
                self.abort(f"步骤数达到上限 {self.max_steps}")
            return None
        start = time.perf_counter()
        try:
            detail = fn(self)
            record = StepRecord(name, True, "" if detail is None else str(detail)[:200])
        except Exception as exc:  # noqa: BLE001 —— 运行时定位就是兜底一切失败
            record = StepRecord(name, False, f"{type(exc).__name__}: {exc}"[:200])
        record.ms = int((time.perf_counter() - start) * 1000)
        self.trajectory.append(record)
        return record

    async def astep(self, name: str, fn: Callable[["AgentRun"], Any]) -> StepRecord | None:
        """异步步骤：fn(run) 可为协程函数或返回可等待对象的普通函数，语义同 step。"""
        import inspect

        if self.aborted or self.steps_used >= self.max_steps:
            if not self.aborted:
                self.abort(f"步骤数达到上限 {self.max_steps}")
            return None
        start = time.perf_counter()
        try:
            detail = fn(self)
            if inspect.iscoroutine(detail):
                detail = await detail
            record = StepRecord(name, True, "" if detail is None else str(detail)[:200])
        except Exception as exc:  # noqa: BLE001
            record = StepRecord(name, False, f"{type(exc).__name__}: {exc}"[:200])
        record.ms = int((time.perf_counter() - start) * 1000)
        self.trajectory.append(record)
        return record

    def summary(self) -> dict[str, Any]:
        """运行结果导出（含完整轨迹），供定时任务结果与日志观测。"""
        failed = [r for r in self.trajectory if not r.ok]
        return {
            "name": self.name,
            "ctx": self.ctx,
            "aborted": self.aborted or None,
            "steps": self.steps_used,
            "failed": len(failed),
            "trajectory": [
                {"step": r.step, "ok": r.ok, "detail": r.detail, "ms": r.ms}
                for r in self.trajectory
            ],
        }


@dataclass
class ToolRegistry:
    """工具登记表：名字 -> 可调用；登记什么、调用什么一眼可见（合规面可查）。"""

    tools: dict[str, Callable[..., Any]] = field(default_factory=dict)

    def register(self, name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
        self.tools[name] = fn
        return fn

    def has(self, name: str) -> bool:
        return name in self.tools

    def call(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """按名调用登记工具；未登记直接抛 KeyError（登记遗漏属于程序错误，应尽早暴露）。"""
        if name not in self.tools:
            raise KeyError(f"未登记的工具：{name}")
        return self.tools[name](*args, **kwargs)
