"""查询优化器。

目前实现的优化策略（相对简单，符合演示系统定位）：

1. 过滤条件尽量下发 — decomposer 已经把 category='X' 注入 WHERE，
   保证分站点先过滤再返回，减少网络传输。
2. 同站点 JOIN 不下发 — 若所有 JOIN 涉及的表都在同一站点（books + borrow_records），
   让子查询在本地完成 JOIN，不把两侧数据都拉到主站点做内存 JOIN。
3. 并行执行 — executor 层面异步并发下发。

optimizer 模块目前主要做日志标记，便于在前端分解步骤中显示"优化动作"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .decomposer import DecompositionPlan


@dataclass
class OptimizationResult:
    plan: DecompositionPlan
    notes: List[str]


def optimize(plan: DecompositionPlan) -> OptimizationResult:
    """对已分解的计划应用优化，返回带说明的结果。"""
    notes: List[str] = []

    # 优化 1：注入 category 过滤条件（已在 decomposer 中实现，这里记录）
    notes.append("过滤条件（category='X'）下发到分站点，减少网络传输")

    # 优化 2：多站点 → 并行执行
    if len(plan.sub_queries) > 1:
        notes.append(f"并行下发到 {len(plan.sub_queries)} 个分站点")

    # 优化 3：单站点直接本地执行，无需主站点聚合
    if len(plan.sub_queries) == 1:
        notes.append("单站点查询，结果直接返回，无需 UNION 聚合")

    return OptimizationResult(plan=plan, notes=notes)
