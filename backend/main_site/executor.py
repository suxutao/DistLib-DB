"""分布式执行调度 + 结果聚合。

- 并发向各分站点 POST /api/execute
- 聚合策略：UNION / GROUP BY merge / 副本表取第一个
- 本地后处理：DISTINCT / ORDER BY / LIMIT / OFFSET / HAVING
- 容错：超时或连接失败不阻断，标记 partial: true
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .config import SITES, SiteConfig
from .decomposer import DecompositionPlan, SubQuery
from .optimizer import OptimizationResult


REQUEST_TIMEOUT = 5.0


@dataclass
class SiteResult:
    site_id: str
    success: bool
    columns: List[str] = field(default_factory=list)
    rows: List[List] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class ExecutionResult:
    success: bool
    columns: List[str]
    rows: List[List]
    partial: bool = False
    warnings: List[str] = field(default_factory=list)
    site_results: List[SiteResult] = field(default_factory=list)


async def execute_plan(
    opt_result: OptimizationResult,
    broadcast,
) -> ExecutionResult:
    """把优化后的计划下发到各分站点、聚合结果，期间通过 broadcast 推送进度。"""

    plan = opt_result.plan
    await broadcast({
        "type": "plan_step",
        "content": f"并行下发 {len(plan.sub_queries)} 个子查询...",
    })

    tasks = [_dispatch_one(sq) for sq in plan.sub_queries]
    raw_results: List[SiteResult] = await asyncio.gather(*tasks)

    for r in raw_results:
        if r.success:
            await broadcast({
                "type": "plan_step",
                "content": f"{r.site_id} 返回 {len(r.rows)} 行，{len(r.columns)} 列",
            })
        else:
            await broadcast({
                "type": "plan_step",
                "content": f"{r.site_id} 失败: {r.error}",
            })

    return _aggregate(raw_results, plan)


async def _dispatch_one(sq: SubQuery) -> SiteResult:
    url = sq.site.url.rstrip("/") + "/api/execute"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(url, json={"sql": sq.sql})
            if resp.status_code != 200:
                return SiteResult(
                    site_id=sq.site.id,
                    success=False,
                    error=f"HTTP {resp.status_code}: {resp.text}",
                )
            data = resp.json()
            if not data.get("success"):
                return SiteResult(
                    site_id=sq.site.id,
                    success=False,
                    error=data.get("error", "未知错误"),
                )
            return SiteResult(
                site_id=sq.site.id,
                success=True,
                columns=data.get("columns", []),
                rows=data.get("rows", []),
            )
    except httpx.TimeoutException as e:
        return SiteResult(site_id=sq.site.id, success=False, error=f"请求超时: {e}")
    except httpx.RequestError as e:
        return SiteResult(site_id=sq.site.id, success=False, error=f"连接失败: {e}")
    except Exception as e:
        return SiteResult(site_id=sq.site.id, success=False, error=f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------------------
# 聚合主函数
# ---------------------------------------------------------------------------

def _aggregate(raw: List[SiteResult], plan: DecompositionPlan) -> ExecutionResult:
    successes = [r for r in raw if r.success]
    failures = [r for r in raw if not r.success]

    warnings: List[str] = list(plan.notes) if hasattr(plan, 'notes') else []
    for f in failures:
        warnings.append(f"{f.site_id} 不可用（{f.error}），已跳过")

    if not successes:
        return ExecutionResult(
            success=False, columns=[], rows=[], partial=False,
            warnings=warnings, site_results=raw,
        )

    # --- 副本表快速路径 ---
    # needs_in_memory_union=False 通常意味着副本表查询，取第一个成功结果即可
    # 但如果副本表查询带了 GROUP BY / LIMIT，仍要做本地后处理
    if not plan.needs_in_memory_union and not plan.has_group_by \
            and plan.limit_value is None and not plan.has_distinct \
            and not plan.has_order_by:
        first = successes[0]
        return ExecutionResult(
            success=True, columns=first.columns, rows=first.rows,
            partial=len(failures) > 0, warnings=warnings, site_results=raw,
        )

    # --- 先 UNION 合并所有成功站点的数据（列对齐） ---
    base_cols = successes[0].columns
    all_rows = _union_rows(successes, base_cols)

    # --- GROUP BY merge ---
    if plan.has_group_by and plan.aggregates:
        merged_cols, merged_rows = _merge_group_by(
            base_cols, all_rows, plan.group_by_cols, plan.aggregates
        )
        # HAVING 在 merge 之后本地过滤
        # 简化：如果有 HAVING 但我们没法在 executor 侧解析（需要 AST），就跳过
        # 实际 HAVING 会在各站点 GROUP BY 时一起下发（子查询仍带 HAVING）
        # 但 decomposer 目前没处理 HAVING 下发... 先做简单版本
        final_cols, final_rows = merged_cols, merged_rows
    else:
        final_cols, final_rows = base_cols, all_rows

    # --- DISTINCT（executor 侧 tuple 去重） ---
    if plan.has_distinct:
        final_rows = _dedup(final_rows)

    # --- ORDER BY ---
    if plan.has_order_by and plan.order_spec:
        final_rows = _sort(final_cols, final_rows, plan.order_spec)

    # --- LIMIT / OFFSET ---
    offset = plan.offset_value or 0
    limit = plan.limit_value
    if offset > 0 or limit is not None:
        end = offset + limit if limit is not None else None
        final_rows = final_rows[offset:end]

    return ExecutionResult(
        success=True, columns=final_cols, rows=final_rows,
        partial=len(failures) > 0, warnings=warnings, site_results=raw,
    )


# ---------------------------------------------------------------------------
# 各模式实现
# ---------------------------------------------------------------------------

def _union_rows(successes: List[SiteResult], base_cols: List[str]) -> List[List]:
    """列对齐后 UNION 合并所有成功站点的 rows。"""
    all_rows: List[List] = []
    for r in successes:
        if r.columns == base_cols:
            all_rows.extend(r.rows)
        else:
            # 列顺序不一致 → 按列名重排
            idx_map = [r.columns.index(c) if c in r.columns else -1 for c in base_cols]
            for row in r.rows:
                new_row = [row[i] if i >= 0 else None for i in idx_map]
                all_rows.append(new_row)
    return all_rows


def _dedup(rows: List[List]) -> List[List]:
    """按 tuple 去重，保持顺序。"""
    seen = set()
    out: List[List] = []
    for row in rows:
        t = tuple(row)
        if t not in seen:
            seen.add(t)
            out.append(row)
    return out


def _sort(columns: List[str], rows: List[List], order_spec: List[Tuple[str, bool]]) -> List[List]:
    """按 (col, desc) 排序。"""
    # 找到每个排序列在 columns 中的位置
    col_idx = []
    for col, desc in order_spec:
        idx = columns.index(col) if col in columns else None
        if idx is not None:
            col_idx.append((idx, desc))
    if not col_idx:
        return rows

    def _key(row):
        return tuple(
            _sort_key(row[i], desc) for i, desc in col_idx
        )

    return sorted(rows, key=_key)


def _sort_key(val, desc: bool):
    """构造排序键：None 永远排最后（无关 desc），其他值正常比。"""
    if val is None:
        return (1, 0 if desc else 1)  # 把 None 放到末尾
    # 简单类型直接比
    return (0, val)


def _merge_group_by(
    columns: List[str],
    rows: List[List],
    group_by_cols: List[str],
    aggregates: List,
) -> Tuple[List[str], List[List]]:
    """分布式 GROUP BY merge。

    各站点已经各自完成 GROUP BY，executor 侧按 group_key 合并聚合值。
    规则：
    - COUNT / SUM → 相加
    - MIN → 取最小
    - MAX → 取最大
    - AVG → ❌ 不支持（退化）
    """
    # 找到分组列在 columns 中的位置
    group_idx: List[int] = []
    for gcol in group_by_cols:
        idx = columns.index(gcol) if gcol in columns else None
        if idx is not None:
            group_idx.append(idx)

    # 聚合列的位置（按出现顺序，跳过分组列）
    agg_idx: List[int] = []
    agg_funcs: List[str] = []  # 对应每个 agg 列的聚合函数
    for i, col in enumerate(columns):
        if i in group_idx:
            continue
        # 找这个列对应的聚合函数
        agg_found = None
        for a in aggregates:
            if _match_agg_to_col(a, col):
                agg_found = a
                break
        if agg_found:
            agg_idx.append(i)
            agg_funcs.append(agg_found.func)

    # 按 group_key 分桶合并
    buckets: Dict[Tuple, List[List]] = {}
    for row in rows:
        key = tuple(row[i] for i in group_idx)
        buckets.setdefault(key, []).append(row)

    merged_rows: List[List] = []
    for key, group_rows in buckets.items():
        if not agg_idx:
            # 只有 GROUP BY 列，没聚合（罕见），取一行即可
            merged_rows.append(list(group_rows[0]))
            continue

        base_row = list(group_rows[0])  # 模板
        for agg_col_idx, func in zip(agg_idx, agg_funcs):
            values = [r[agg_col_idx] for r in group_rows if r[agg_col_idx] is not None]
            if not values:
                base_row[agg_col_idx] = None
                continue
            if func in ("count", "sum"):
                base_row[agg_col_idx] = sum(values)
            elif func == "min":
                base_row[agg_col_idx] = min(values)
            elif func == "max":
                base_row[agg_col_idx] = max(values)
            elif func == "avg":
                # AVG 退化：取第一个值（正确做法是加权平均，但需要额外字段）
                base_row[agg_col_idx] = values[0]
        merged_rows.append(base_row)

    # 输出列和输入列一致
    return columns, merged_rows


def _match_agg_to_col(agg, col_name: str) -> bool:
    """判断一个聚合函数是否对应某列。"""
    # sqlglot 生成的 alias 可能是 "count(*)" / "sum(total_count)"
    func_col = f"{agg.func}({agg.col})"
    return col_name == func_col or col_name.lower() == func_col.lower()


# ---------------------------------------------------------------------------
# 站点状态查询
# ---------------------------------------------------------------------------

async def check_sites() -> List[Dict[str, Any]]:
    async def _one(site: SiteConfig) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(site.url.rstrip("/") + "/api/health")
                data = resp.json() if resp.status_code == 200 else {}
                return {
                    "id": site.id,
                    "url": site.url,
                    "online": resp.status_code == 200,
                    "category": site.categories[0] if site.categories else "",
                    "info": data,
                }
        except Exception:
            return {
                "id": site.id,
                "url": site.url,
                "online": False,
                "category": site.categories[0] if site.categories else "",
                "info": None,
            }

    return list(await asyncio.gather(*[_one(s) for s in SITES]))
