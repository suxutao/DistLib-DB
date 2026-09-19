"""主站点 FastAPI 入口。

启动方式：
    conda activate distlib
    python -m backend.main_site.main
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.main_site.config import SITES  # noqa: E402
from backend.main_site.decomposer import decompose  # noqa: E402
from backend.main_site.executor import check_sites, execute_plan  # noqa: E402
from backend.main_site.optimizer import optimize  # noqa: E402
from backend.main_site.parser import parse_sql  # noqa: E402


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="分布式图书馆 · 主站点", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket 管理器
# ---------------------------------------------------------------------------

class MonitorManager:
    def __init__(self) -> None:
        self._connections: List[WebSocket] = []
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        async with self._lock:
            self._connections.append(ws)

    async def disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            if ws in self._connections:
                self._connections.remove(ws)

    async def broadcast(self, message: dict) -> None:
        dead: List[WebSocket] = []
        async with self._lock:
            snapshot = list(self._connections)
        for ws in snapshot:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    if ws in self._connections:
                        self._connections.remove(ws)


manager = MonitorManager()


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


async def _broadcast(content: str, msg_type: str = "plan_step") -> None:
    await manager.broadcast({"type": msg_type, "timestamp": _now(), "content": content})


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------

class QueryRequest(BaseModel):
    sql: str


class SubQueryOut(BaseModel):
    site: str
    sql: str


class QueryPlan(BaseModel):
    original_sql: str
    involved_sites: List[str]
    sub_queries: List[SubQueryOut]
    steps: List[str]
    notes: List[str] = []


class QueryResponse(BaseModel):
    success: bool
    partial: bool = False
    warnings: List[str] = []
    columns: List[str] = []
    rows: List[List] = []
    plan: Optional[QueryPlan] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# POST /api/query
# ---------------------------------------------------------------------------

@app.post("/api/query", response_model=QueryResponse)
async def query(req: QueryRequest) -> QueryResponse:
    steps: List[str] = []

    await _broadcast(f"接收用户 SQL: {req.sql}")

    # 1) 解析
    try:
        pq = parse_sql(req.sql)
    except ValueError as e:
        await _broadcast(f"❌ SQL 解析失败: {e}")
        return QueryResponse(success=False, error=str(e))

    await _broadcast("SQL 解析完成 ✓")
    steps.append("SQL 解析完成")

    # 2) 分解
    plan = decompose(pq)
    await _broadcast(
        f"确定涉及站点: {[s.id for s in plan.involved_sites]}"
    )
    steps.append(
        f"确定涉及站点: {', '.join(s.id for s in plan.involved_sites)}"
    )

    for sq in plan.sub_queries:
        await _broadcast(f"生成子查询 → {sq.site.id}: {sq.sql}")
    steps.append("生成子查询")

    # 3) 优化
    opt = optimize(plan)
    for n in opt.notes:
        await _broadcast(f"优化: {n}")
    steps.append("优化完成")

    # 4) 执行
    exec_result = await execute_plan(opt, _broadcast)
    steps.append("执行调度完成")

    if exec_result.partial:
        steps.append("部分分站点未响应，返回已聚合结果")
    steps.append("结果聚合完成")

    sub_queries_out = [
        SubQueryOut(site=sq.site.id, sql=sq.sql) for sq in plan.sub_queries
    ]

    return QueryResponse(
        success=exec_result.success,
        partial=exec_result.partial,
        warnings=exec_result.warnings,
        columns=exec_result.columns,
        rows=exec_result.rows,
        plan=QueryPlan(
            original_sql=plan.original_sql,
            involved_sites=[s.id for s in plan.involved_sites],
            sub_queries=sub_queries_out,
            steps=steps,
            notes=opt.notes,
        ),
    )


# ---------------------------------------------------------------------------
# GET /api/sites
# ---------------------------------------------------------------------------

@app.get("/api/sites")
async def sites() -> dict:
    results = await check_sites()
    return {"sites": results}


# ---------------------------------------------------------------------------
# WebSocket /ws/monitor
# ---------------------------------------------------------------------------

@app.websocket("/ws/monitor")
async def ws_monitor(ws: WebSocket) -> None:
    await manager.connect(ws)
    await ws.send_json(
        {"type": "info", "timestamp": _now(), "content": "已连接到主站点监控通道"}
    )
    # 纯推送模式，挂起直到断开
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await manager.disconnect(ws)


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------

@app.get("/")
async def root() -> dict:
    return {
        "service": "DistLib-DB 主站点",
        "version": "1.0.0",
        "sites": [{"id": s.id, "url": s.url, "categories": s.categories} for s in SITES],
    }


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main() -> None:
    print("🚀 DistLib-DB 主站点启动 — http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    main()
