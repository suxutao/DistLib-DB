"""查询分解器。

根据 ParsedQuery 中的信息判断查询涉及哪些分站点，并为每个站点生成子查询。
GROUP BY / 聚合查询也能正常路由，子查询会被剥掉 LIMIT/OFFSET（由 executor 最后统一处理）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from .config import (
    PARTITION_COLUMN,
    PARTITION_TABLE,
    REPLICATED_TABLES,
    SITES,
    SiteConfig,
    category_to_site,
    sites_for_categories,
)
from .parser import (
    AggInfo,
    ParsedQuery,
    add_category_predicate,
    ast_to_sql,
)


@dataclass
class SubQuery:
    site: SiteConfig
    sql: str


@dataclass
class DecompositionPlan:
    original_sql: str
    involved_sites: List[SiteConfig]
    sub_queries: List[SubQuery]

    # 聚合/分页信息（executor 用来做 merge 和本地 slice）
    has_group_by: bool = False
    group_by_cols: List[str] = field(default_factory=list)
    aggregates: List[AggInfo] = field(default_factory=list)
    has_having: bool = False
    limit_value: Optional[int] = None
    offset_value: Optional[int] = None
    has_distinct: bool = False
    has_order_by: bool = False
    order_spec: List = field(default_factory=list)

    # executor 侧决定如何聚合
    # - True: 各站点返回行数据 → UNION/merge
    # - False: 副本表，executor 取第一个成功结果
    needs_in_memory_union: bool = True


def _strip_limit_offset(ast) -> "exp.Select":
    """剥掉 AST 里的 LIMIT / OFFSET，返回副本。"""
    new_ast = ast.copy()
    new_ast.set("limit", None)
    new_ast.set("offset", None)
    return new_ast


def decompose(pq: ParsedQuery) -> DecompositionPlan:
    """把解析后的查询分解为若干子查询，每个子查询下发到一个分站点。"""

    tables_lower = {t.lower() for t in pq.tables}

    # 准备：用于子查询的 AST（剥掉 LIMIT/OFFSET）
    base_ast = _strip_limit_offset(pq.ast) if (pq.limit_value is not None or pq.offset_value is not None) else pq.ast

    # 1) 只查副本表（readers）— 全部下发作为冗余
    if tables_lower <= REPLICATED_TABLES:
        sub_sql = ast_to_sql(base_ast)
        sub_queries = [SubQuery(site=s, sql=sub_sql) for s in SITES]
        return DecompositionPlan(
            original_sql=pq.original_sql,
            involved_sites=list(SITES),
            sub_queries=sub_queries,
            # 聚合/分页信息
            has_group_by=pq.has_group_by,
            group_by_cols=pq.group_by_cols,
            aggregates=pq.aggregates,
            has_having=pq.has_having,
            limit_value=pq.limit_value,
            offset_value=pq.offset_value,
            has_distinct=pq.has_distinct,
            has_order_by=pq.has_order_by,
            order_spec=pq.order_spec,
            needs_in_memory_union=not pq.has_group_by,  # GROUP BY 也要合并
        )

    # 2) 从 WHERE 中明确提取出 category 值
    explicit_cats = pq.sites_categories()

    if explicit_cats is not None and len(explicit_cats) > 0:
        involved = sites_for_categories(explicit_cats)
    else:
        involved = list(SITES)

    has_explicit_category = bool(pq.categories_in_where)

    sub_queries: List[SubQuery] = []
    for site in involved:
        if has_explicit_category:
            sub_sql = ast_to_sql(base_ast)
        else:
            cat = site.categories[0] if site.categories else None
            if cat is None:
                sub_sql = ast_to_sql(base_ast)
            else:
                sub_ast = add_category_predicate(base_ast, cat)
                sub_sql = ast_to_sql(sub_ast)
        sub_queries.append(SubQuery(site=site, sql=sub_sql))

    return DecompositionPlan(
        original_sql=pq.original_sql,
        involved_sites=involved,
        sub_queries=sub_queries,
        has_group_by=pq.has_group_by,
        group_by_cols=pq.group_by_cols,
        aggregates=pq.aggregates,
        has_having=pq.has_having,
        limit_value=pq.limit_value,
        offset_value=pq.offset_value,
        has_distinct=pq.has_distinct,
        has_order_by=pq.has_order_by,
        order_spec=pq.order_spec,
        needs_in_memory_union=len(sub_queries) > 1,
    )
