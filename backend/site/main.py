"""分站点 FastAPI 入口。

启动时通过命令行参数指定端口和负责的图书分类，例如：

    python -m backend.site.main --port 8001 --category 文学
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 保证从仓库根目录运行时，backend 包能被正确导入
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.site.db import execute_sql, init_db  # noqa: E402


# ---------------------------------------------------------------------------
# 配置（延迟初始化，避免 import 时 argparse 崩溃）
# ---------------------------------------------------------------------------

_PORT: Optional[int] = None
_CATEGORY: Optional[str] = None
_DB_PATH: Optional[Path] = None


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="分布式图书馆 — 分站点后端")
    parser.add_argument("--port", type=int, required=True, help="服务端口（8001~8004）")
    parser.add_argument("--category", type=str, required=True, help="负责的图书分类")
    parser.add_argument(
        "--db-dir",
        type=str,
        default=str(_PROJECT_ROOT / "backend" / "site" / "data"),
        help="SQLite 数据库文件存放目录",
    )
    return parser.parse_args(argv)


def _init_config(argv: Optional[List[str]] = None) -> None:
    """解析命令行参数并初始化数据库（只调用一次）。"""
    global _PORT, _CATEGORY, _DB_PATH
    args = _parse_args(argv)
    _PORT = args.port
    _CATEGORY = args.category
    db_dir = Path(args.db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    _DB_PATH = db_dir / f"site_{_PORT}.db"
    init_db(str(_DB_PATH), _CATEGORY)


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(title="DistLib-DB Site", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# WebSocket 管理器
# ---------------------------------------------------------------------------

class ConnectionManager:
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


manager = ConnectionManager()


def _now() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------

class ExecuteRequest(BaseModel):
    sql: str


class ExecuteResponse(BaseModel):
    success: bool
    columns: List[str] = []
    rows: List[List] = []
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# HTTP 接口
# ---------------------------------------------------------------------------

@app.post("/api/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest) -> ExecuteResponse:
    incoming_msg = {
        "type": "command",
        "timestamp": _now(),
        "direction": "incoming",
        "content": req.sql,
    }
    await manager.broadcast(incoming_msg)

    try:
        stripped = req.sql.strip().upper()
        if any(stripped.startswith(k) for k in ("INSERT", "UPDATE", "DELETE", "DROP", "CREATE")):
            raise HTTPException(status_code=400, detail="只支持 SELECT 查询")

        columns, rows = execute_sql(str(_DB_PATH), req.sql)

        await manager.broadcast({
            "type": "command",
            "timestamp": _now(),
            "direction": "outgoing",
            "content": f"返回 {len(rows)} 行，{len(columns)} 列",
        })

        return ExecuteResponse(success=True, columns=columns, rows=rows)
    except Exception as exc:
        await manager.broadcast({
            "type": "command",
            "timestamp": _now(),
            "direction": "error",
            "content": str(exc),
        })
        return ExecuteResponse(success=False, error=str(exc))


@app.get("/api/health")
async def health() -> dict:
    return {
        "ok": True,
        "port": _PORT,
        "category": _CATEGORY,
        "db": str(_DB_PATH),
    }


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/commands")
async def ws_commands(ws: WebSocket) -> None:
    await manager.connect(ws)
    await ws.send_json({
        "type": "info",
        "timestamp": _now(),
        "content": f"已连接到 Site {_PORT}（{_CATEGORY}）",
    })
    # 纯推送模式：不接收前端消息，挂起直到断开
    try:
        await asyncio.Future()
    except asyncio.CancelledError:
        pass
    finally:
        await manager.disconnect(ws)


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

def main() -> None:
    _init_config()
    # 启动时更新 FastAPI 元信息
    app.title = f"分布式图书馆 · Site {_PORT}"
    app.description = f"负责分类: {_CATEGORY}"
    print(f"🚀 Site {_PORT} 启动 — 分类: {_CATEGORY} — DB: {_DB_PATH}")
    uvicorn.run(app, host="0.0.0.0", port=_PORT, log_level="info")


if __name__ == "__main__":
    main()
