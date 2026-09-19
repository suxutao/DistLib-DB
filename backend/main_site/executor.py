"""分布式执行调度 + 结果聚合。

- 并发向各分站点 POST /api/execute
- 聚合返回结果（列对齐、UNION 合并）
- 容错：超时或连接失败不阻断整体流程，在响应中标记 partial: true
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from .config import SITES, SiteConfig
from .decomposer import DecompositionPlan, SubQuery
from .optimizer import OptimizationResult


# 请求超时（秒）
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
    broadcast,  # callable(dict) -> Awaitable[None]
) -> ExecutionResult:
    """把优化后的计划下发到各分站点、聚合结果，期间通过 broadcast 推送进度。"""

    plan = opt_result.plan
    await broadcast(
        {
            "type": "plan_step",
            "content": f"并行下发 {len(plan.sub_queries)} 个子查询...",
        }
    )

    # 并发执行
    tasks = [_dispatch_one(sq) for sq in plan.sub_queries]
    raw_results: List[SiteResult] = await asyncio.gather(*tasks)

    for r in raw_results:
        if r.success:
            await broadcast(
                {
                    "type": "plan_step",
                    "content": f"{r.site_id} 返回 {len(r.rows)} 行，{len(r.columns)} 列",
                }
            )
        else:
            await broadcast(
                {
                    "type": "plan_step",
                    "content": f"{r.site_id} 失败: {r.error}",
                }
            )

    # 聚合
    return _aggregate(raw_results, needs_union=opt_result.plan.needs_in_memory_union)


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


def _aggregate(raw: List[SiteResult], needs_union: bool = True) -> ExecutionResult:
    successes = [r for r in raw if r.success]
    failures = [r for r in raw if not r.success]

    warnings: List[str] = []
    for f in failures:
        warnings.append(f"{f.site_id} 不可用（{f.error}），已跳过")

    if not successes:
        return ExecutionResult(
            success=False,
            columns=[],
            rows=[],
            partial=False,
            warnings=warnings,
            site_results=raw,
        )

    # 副本表场景：所有副本数据相同，取第一个成功的即可
    if not needs_union:
        first = successes[0]
        return ExecutionResult(
            success=True,
            columns=first.columns,
            rows=first.rows,
            partial=len(failures) > 0,
            warnings=warnings,
            site_results=raw,
        )

    # 列对齐：以第一个成功站点的 columns 为准
    base_cols = successes[0].columns

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

    # 简单去重并保持顺序
    seen = set()
    deduped: List[List] = []
    for row in all_rows:
        t = tuple(row)
        if t not in seen:
            seen.add(t)
            deduped.append(row)

    return ExecutionResult(
        success=True,
        columns=base_cols,
        rows=deduped,
        partial=len(failures) > 0,
        warnings=warnings,
        site_results=raw,
    )


# ---------------------------------------------------------------------------
# 站点状态查询
# ---------------------------------------------------------------------------

async def check_sites() -> List[Dict[str, Any]]:
    """查询所有分站点的 /api/health，返回聚合状态。"""
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
