# DistLib-DB · 分布式图书馆数据库

一个演示分布式数据库核心原理的小型系统：48 本书水平划分到 4 个分站点（SQLite），主站点（FastAPI）透明完成 SQL 解析、路由、并行下发与结果聚合。支持实时命令监控、查询分解可视化、站点故障容错降级。

## 🧩 系统架构

```
             ┌──────────────────┐
             │   主站点 (8000)   │
             │  FastAPI + sqlglot │
             │  httpx 并发调度     │
             └────────┬─────────┘
                      │ HTTP REST + WebSocket
    ┌────────┬────────┼────────┬────────┐
    ▼        ▼        ▼        ▼        ▼
 Site A   Site B   Site C   Site D   (frontend)
 (8001)   (8002)   (8003)   (8004)   (5173)
  文学      科技      教育      历史    Vue 3
 books + readers 副本（每站都有完整 15 人）
```

| 组件 | 技术 | 端口 |
|------|------|------|
| 前端 | Vue 3.5 + Vite 6 + vue-router 4 + keep-alive | 5173 |
| 主站点 | FastAPI + sqlglot 30 + httpx（异步并发） | 8000 |
| 分站点 A | FastAPI + SQLite | 8001（文学） |
| 分站点 B | FastAPI + SQLite | 8002（科技） |
| 分站点 C | FastAPI + SQLite | 8003（教育） |
| 分站点 D | FastAPI + SQLite | 8004（历史） |

## 📁 项目结构

```
DistLib‑DB/
├── backend/
│   ├── main_site/              # 主站点（协调者）
│   │   ├── main.py             # FastAPI 入口 + WebSocket 管理器
│   │   ├── config.py           # 分站点配置加载
│   │   ├── config.yaml         # 4 个分站点的 URL / category 映射
│   │   ├── parser.py           # sqlglot SQL 解析 → AST + 语义分析
│   │   ├── decomposer.py       # 路由策略：根据 category 字段决定下发到哪些站点
│   │   ├── optimizer.py        # 优化标记（并行、单站点短路等）
│   │   └── executor.py         # httpx.AsyncClient 并发下发 + 结果聚合
│   └── site/                   # 通用分站点（数据节点）
│       ├── main.py             # FastAPI 入口 + WebSocket 纯推送模式
│       └── db.py               # SQLite 初始化 + 48 本示例数据
│       └── data/               # ⭐ SQLite 文件存放目录（运行时自动生成）
│           ├── site_8001.db    # 文学：12 本书 + 15 借阅 + 15 读者副本
│           ├── site_8002.db    # 科技：12 本书 + 14 借阅 + 15 读者副本
│           ├── site_8003.db    # 教育：12 本书 + 14 借阅 + 15 读者副本
│           └── site_8004.db    # 历史：12 本书 + 14 借阅 + 15 读者副本
├── frontend/                   # Vue 3 前端
│   └── src/
│       ├── App.vue             # 侧边栏布局 + keep-alive 页面缓存
│       ├── router/index.js     # 路由：/ 主站点，/site/:port 分站点
│       └── views/
│           ├── MainSite.vue    # 主站点 3 Tab：查询控制台 + 分解过程 + 站点监控
│           └── SiteMonitor.vue # 分站点实时命令监控 + 健康状态
├── start.bat                   # ⭐ Windows 一键启动（自动建 conda 环境 + 装依赖）
├── stop.bat                    # ⭐ Windows 一键停止（扫端口杀进程）
├── 产品文档.md                 # 原始需求设计文档
└── README.md
```

## 🗂️ 数据划分策略

三张表的分布体现了**分片 + 副本混合架构**：

| 表 | 分布类型 | 存储位置 | 冗余 |
|----|---------|---------|------|
| **books** | 水平分片（按 category） | 文学→8001，科技→8002，教育→8003，历史→8004 | 0 |
| **borrow_records** | 同址分片（colocate） | 跟随 books：book_id=1 的借阅在 8001 | 0 |
| **readers** | 读副本（read-only copy） | 每站都有完整 15 人副本 | 4× 冗余 |

副本表的意义：**任意关闭 3 个分站点，SELECT * FROM readers 仍能从存活的站点返回数据**。

## 🧠 查询处理全链路

```
用户 SQL ──► ① Parser ──► ② Decomposer ──► ③ Optimizer ──► ④ Executor ──► ⑤ Aggregator ──► ⑥ 前端
```

| 步骤 | 模块 | 核心逻辑 |
|------|------|---------|
| ① | `parser.py` | sqlglot → AST → 校验（拒绝 GROUP BY / LIMIT / 子查询）+ 提取 category 值 |
| ② | `decomposer.py` | 路由策略（见下）+ 无 category 条件时 AST 层面注入 `AND category='X'` |
| ③ | `optimizer.py` | 标记优化点（并行、单站点短路等） |
| ④ | `executor.py` | `httpx.AsyncClient` + `asyncio.gather()` 并发下发，5s 超时 |
| ⑤ | `executor.py` | 列对齐 → UNION 合并 → tuple 去重 → 标记 `partial: true` |
| ⑥ | 前端 | Tab 1 分页表格 + Tab 2 实时分解日志（WebSocket + 自动贴底） |

### 路由决策表（Decomposer 核心）

| 查询条件 | 下发目标 | SQL 改写 |
|---------|---------|---------|
| `SELECT * FROM readers` | 全部 4 站（谁先返回用谁） | 原样，副本表场景取第一个成功结果 |
| `WHERE category = '文学'` | **只发 8001**（单点路由） | 原样 |
| `WHERE category IN ('文学','科技')` | **发 8001 + 8002**（多点路由） | 原样（信任 IN 自然过滤） |
| 无 category 条件 | **发全部 4 站**（广播） | 追加 `AND category='X'` |

### 容错机制

| 故障 | 行为 |
|------|------|
| 单站点 5s 超时 | 跳过，`partial: true`，warnings 标明哪站挂了 |
| 所有站点挂了 | `success: false` |
| 副本表查询 + 3 站挂了 | 仍能从存活的站点返回完整 readers 数据 |

## 🛠️ 环境要求

- **Python 3.10+**（推荐 conda 环境 `libDB`）
- **Node.js 18+** + **npm 9+**
- 端口（8000-8004, 5173）未被占用

### Python 依赖

```bash
conda create -n libDB python=3.11 -y
conda activate libDB
pip install fastapi uvicorn sqlglot httpx pyyaml
```

### 前端依赖

```bash
cd frontend
npm install
```

## 🚀 启动

### 方式一：一键启动（Windows，推荐）

**双击 `start.bat`**：自动建 conda 环境、装依赖、拉起 6 个服务。启动后 5 秒原窗口自关。

### 方式二：手动分步（PowerShell，6 个终端）

```powershell
# ── 分站点 × 4 ──
conda activate libDB
python -m backend.site.main --port 8001 --category 文学
```
```powershell
conda activate libDB
python -m backend.site.main --port 8002 --category 科技
```
```powershell
conda activate libDB
python -m backend.site.main --port 8003 --category 教育
```
```powershell
conda activate libDB
python -m backend.site.main --port 8004 --category 历史
```
```powershell
# ── 主站点 ──
conda activate libDB
python -m backend.main_site.main
```
```powershell
# ── 前端 ──
cd frontend
npm run dev
```

### 停止服务

```powershell
# 一键停止（推荐，扫端口杀进程）
.\stop.bat

# 或手动
Get-NetTCPConnection -LocalPort 8000,8001,8002,8003,8004,5173 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
```

## ✅ 验证

| 地址 | 说明 |
|------|------|
| http://localhost:5173 | **前端主页面**（推荐入口） |
| http://localhost:8000 | 主站点健康检查 |
| http://localhost:8001/api/health | Site A 健康检查 |

### 命令行快速测试

```powershell
# 跨站点查询
$body = '{"sql": "SELECT title, author, category FROM books WHERE category IN (''文学'', ''科技'')"}'
Invoke-RestMethod -Uri http://localhost:8000/api/query -Method Post -ContentType 'application/json' -Body $body | ConvertTo-Json -Depth 5

# 副本表查询（关一个分站点也能返回）
$body2 = '{"sql": "SELECT * FROM readers"}'
Invoke-RestMethod -Uri http://localhost:8000/api/query -Method Post -ContentType 'application/json' -Body $body2 | ConvertTo-Json -Depth 5
```

## 📊 支持的 SQL 子集

| ✅ 支持 | ❌ 不支持 |
|--------|----------|
| SELECT (指定列 / `*`) | 子查询 / 嵌套 SELECT |
| WHERE (`=`, `IN`, `LIKE`, `>`, `<`, `>=`, `<=`, `!=`, `AND`, `OR`) | GROUP BY / 聚合函数 |
| INNER JOIN（同站点） | LIMIT / OFFSET |
| ORDER BY（单字段） | DISTINCT |
| 列别名 | 写操作（INSERT/UPDATE/DELETE） |

## 🔗 API 契约

### 主站点 (8000)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/query` | 提交 SQL，返回 `{ success, columns, rows, partial, warnings, plan }` |
| WS   | `/ws/monitor` | 推送查询分解实时日志（plan_step / command / info） |
| GET  | `/api/sites` | 获取所有分站点在线状态 + health 信息 |

### 分站点 (8001-8004)

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/execute` | 接收子查询，本地 SQLite 执行返回 `{ success, columns, rows, error }` |
| GET  | `/api/health` | 返回 `{ ok, port, category, db }` |
| WS   | `/ws/commands` | 纯推送模式，下发命令时广播（incoming / outgoing / error） |

## 🖥️ 前端功能

### 主站点（3 Tab）

| Tab | 功能 |
|-----|------|
| **查询控制台** | SQL 输入 + 示例下拉 + 结果表格（前端分页，10/20/50/100 条/页） |
| **分解过程** | WebSocket 实时分解日志（自动贴底、保留不被清空）+ 计划详情（原始 SQL / 涉及站点 / 子查询 / 优化动作） |
| **站点监控** | 4 个分站点健康状态卡片（在线/离线、端口、分类） |

### 分站点监控

| 功能 | 说明 |
|------|------|
| 命令实时日志 | WebSocket 接收主站点下发的子查询，区分 ⬇️ 接收 / ⬆️ 返回 / ❌ 错误，自动贴底 |
| 过滤器 | 接收 / 返回 / 错误 三种方向可独立开关 |
| 统计计数器 | 已处理 / 成功 / 失败 实时累加 |
| 健康状态 | 3s 轮询 `/api/health`，绿点=在线，红点=离线 |

### 页面状态保持

- `<keep-alive :key="route.fullPath">` 缓存每个路由实例
- `/site/8001` → `/site/8002` 各自独立缓存，日志/状态互不干扰
- 跨页面切换不丢数据，WebSocket 在后台标签页持续接收

## 🐛 常见问题

**Q: 分站点启动报 ModuleNotFoundError: No module named 'backend'**
A: 必须在项目根目录执行，不要 cd 到 backend/site 里运行。

**Q: 某个分站点未启动，查询仍能返回但 partial=true**
A: 正常的降级行为——executor 5s 超时后跳过该站点，warnings 字段标明。

**Q: 副本表 readers 查询在 Site A 挂了后仍能返回**
A: 正常！副本表查询会同时下发到全部 4 站，谁先返回用谁。

**Q: 实时日志窗口不自动贴底？**
A: 三重保险：收到消息立即滚 / 切 Tab 时滚 / keep-alive 切回时滚。如果还不行检查浏览器控制台。

**Q: 分站点每重启数据会重置？**
A: 是的。`init_db()` 启动时删旧 .db 重建，保证每次演示数据一致。想保留持久化可删掉 `os.remove(db_path)` 那行。
