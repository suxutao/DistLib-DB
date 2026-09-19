"""查询分解器。

根据 ParsedQuery 中的信息判断查询涉及哪些分站点，并为每个站点生成子查询。
"""

from __future__ import annotations

from dataclasses import dataclass
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
from .parser import ParsedQuery, add_category_predicate, ast_to_sql


@dataclass
class SubQuery:
    site: SiteConfig
    sql: str


@dataclass
class DecompositionPlan:
    original_sql: str
    involved_sites: List[SiteConfig]
    sub_queries: List[SubQuery]
    # 是否需要在主站点做内存 UNION 聚合（同一列集合的简单拼接）
    # 目前所有跨站点查询都走 UNION，留作扩展点
    needs_in_memory_union: bool = True


def decompose(pq: ParsedQuery) -> DecompositionPlan:
    """把解析后的查询分解为若干子查询，每个子查询下发到一个分站点。

    规则：
    - 只查询 readers（副本表） → 任选一个站点即可
    - 明确 WHERE category='X'   → 只下发到对应站点
    - 明确 WHERE category IN (...) → 下发到涉及的每个站点
    - 其它（无 category 条件 / 或非 category 条件） → 下发到全部 4 个站点
    """

    tables_lower = {t.lower() for t in pq.tables}

    # 1) 只查副本表（readers）— 下发到所有副本节点，executor 侧取第一个成功的
    if tables_lower <= REPLICATED_TABLES:
        sub_sql = ast_to_sql(pq.ast)
        # 副本表每站都有，全部下发作为冗余备份，谁先返回用谁
        sub_queries = [SubQuery(site=s, sql=sub_sql) for s in SITES]
        return DecompositionPlan(
            original_sql=pq.original_sql,
            involved_sites=list(SITES),
            sub_queries=sub_queries,
            needs_in_memory_union=False,  # executor 取第一个成功的，不 UNION
        )

    # 2) 从 WHERE 中明确提取出 category 值
    explicit_cats = pq.sites_categories()

    if explicit_cats is not None and len(explicit_cats) > 0:
        involved = sites_for_categories(explicit_cats)
    else:
        # 3) 没有明确 category 条件 → 涉及所有分片站点
        involved = list(SITES)

    # 为每个涉及站点生成子查询
    # - 如果原查询已有 category 条件（IN / =），直接下发原 SQL（信任它会自然过滤）
    # - 只有原查询完全没 category 条件时，才注入 AND category = 'X'
    has_explicit_category = bool(pq.categories_in_where)

    sub_queries: List[SubQuery] = []
    for site in involved:
        if has_explicit_category:
            # 原 SQL 已有 category 条件，直接下发，不再追加
            sub_sql = ast_to_sql(pq.ast)
        else:
            cat = site.categories[0] if site.categories else None
            if cat is None:
                sub_sql = ast_to_sql(pq.ast)
            else:
                sub_ast = add_category_predicate(pq.ast, cat)
                sub_sql = ast_to_sql(sub_ast)
        sub_queries.append(SubQuery(site=site, sql=sub_sql))

    return DecompositionPlan(
        original_sql=pq.original_sql,
        involved_sites=involved,
        sub_queries=sub_queries,
        needs_in_memory_union=len(sub_queries) > 1,
    )
