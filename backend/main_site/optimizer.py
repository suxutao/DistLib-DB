"""查询优化器。

生成优化说明供前端展示，核心逻辑都在 decomposer / executor 里完成。
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

    # 优化 1：注入 category 过滤条件（已在 decomposer 中实现）
    notes.append("过滤条件（category='X'）下发到分站点，减少网络传输")

    # 优化 2：多站点 → 并行执行
    if len(plan.sub_queries) > 1:
        notes.append(f"并行下发到 {len(plan.sub_queries)} 个分站点")
    else:
        notes.append("单站点查询，直接返回")

    # 优化 3：GROUP BY 分布式聚合
    if plan.has_group_by:
        funcs = ",".join(sorted({a.func.upper() for a in plan.aggregates})) if plan.aggregates else ""
        if funcs:
            notes.append(
                f"GROUP BY 分布式 merge：各站点本地聚合（{funcs}），"
                f"executor 侧按分组键合并聚合值"
            )
        else:
            notes.append("GROUP BY：各站点本地分组，executor 侧合并")

    # 优化 4：LIMIT/OFFSET 本地统一处理
    if plan.limit_value is not None or plan.offset_value is not None:
        notes.append(
            "子查询不带 LIMIT/OFFSET（保证各站点返回完整数据），"
            "由主站点统一 slice 保证分页正确性"
        )

    # 优化 5：ORDER BY 本地排序
    if plan.has_order_by:
        notes.append("ORDER BY 由主站点内存排序（各站点数据合并后再排序）")

    # 优化 6：DISTINCT 本地去重
    if plan.has_distinct:
        notes.append("DISTINCT：主站点 tuple 去重（UNION 默认行为）")

    return OptimizationResult(plan=plan, notes=notes)
