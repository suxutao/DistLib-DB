"""SQL 解析与语义分析。

使用 sqlglot 将用户 SQL 解析为 AST，提取：
- 涉及的表
- WHERE 条件中出现的 category 值（用于判断涉及哪些分站点）
- GROUP BY / 聚合函数 / HAVING / LIMIT / DISTINCT 等信息
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


# 允许的表（用于语义检查）
ALLOWED_TABLES = {"books", "borrow_records", "readers"}

# 支持的聚合函数
SUPPORTED_AGGS = {"count", "sum", "min", "max", "avg"}


@dataclass
class AggInfo:
    """聚合函数信息。"""
    func: str          # COUNT / SUM / MIN / MAX / AVG
    col: str           # 作用的列名，* 代表 COUNT(*)
    is_distinct: bool  # COUNT(DISTINCT x)


@dataclass
class ParsedQuery:
    """解析后的查询信息。"""

    original_sql: str
    ast: exp.Select
    tables: Set[str] = field(default_factory=set)
    categories_in_where: Set[str] = field(default_factory=set)
    has_category_column: bool = False

    # 新增：高级特性
    has_group_by: bool = False
    group_by_cols: List[str] = field(default_factory=list)
    aggregates: List[AggInfo] = field(default_factory=list)
    has_having: bool = False
    limit_value: Optional[int] = None
    offset_value: Optional[int] = None
    has_distinct: bool = False
    has_order_by: bool = False
    order_spec: List[Tuple[str, bool]] = field(default_factory=list)  # (col, desc)

    def sites_categories(self) -> Optional[List[str]]:
        """返回 WHERE 条件中明确限定的 category 值列表；若无则返回 None。"""
        if not self.categories_in_where:
            return None
        return sorted(self.categories_in_where)


def parse_sql(sql: str) -> ParsedQuery:
    """解析 SQL 并进行语义分析。"""
    sql = sql.strip().rstrip(";").strip()

    try:
        statements = sqlglot.parse(sql, dialect="sqlite")
    except ParseError as e:
        raise ValueError(f"SQL 语法错误: {e}") from e

    if not statements or statements[0] is None:
        raise ValueError("SQL 为空")
    ast = statements[0]

    if not isinstance(ast, exp.Select):
        raise ValueError("只支持 SELECT 查询")

    _validate(ast)

    tables = _extract_tables(ast)
    cats = _extract_category_values(ast)
    has_cat = _has_category_in_select(ast)

    # 高级特性提取
    has_group_by, group_by_cols = _extract_group_by(ast)
    aggregates = _extract_aggregates(ast)
    has_having = ast.args.get("having") is not None
    limit_val, offset_val = _extract_limit_offset(ast)
    has_distinct = ast.args.get("distinct") is not None
    has_order_by, order_spec = _extract_order_by(ast)

    return ParsedQuery(
        original_sql=sql,
        ast=ast,
        tables=tables,
        categories_in_where=cats,
        has_category_column=has_cat,
        has_group_by=has_group_by,
        group_by_cols=group_by_cols,
        aggregates=aggregates,
        has_having=has_having,
        limit_value=limit_val,
        offset_value=offset_val,
        has_distinct=has_distinct,
        has_order_by=has_order_by,
        order_spec=order_spec,
    )


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _validate(ast: exp.Select) -> None:
    """拒绝嵌套子查询"""
    if ast.find(exp.Subquery):
        raise ValueError("不支持嵌套子查询（可先拆成两条独立语句）")


# ---------------------------------------------------------------------------
# GROUP BY 提取
# ---------------------------------------------------------------------------

def _extract_group_by(ast: exp.Select) -> Tuple[bool, List[str]]:
    group = ast.args.get("group")
    if group is None:
        return False, []
    cols: List[str] = []
    for g in group.expressions:
        if isinstance(g, exp.Column):
            cols.append(g.name)
        elif isinstance(g, exp.Alias):
            # GROUP BY 别名
            cols.append(g.alias)
    return len(cols) > 0, cols


# ---------------------------------------------------------------------------
# 聚合函数提取
# ---------------------------------------------------------------------------

def _extract_aggregates(ast: exp.Select) -> List[AggInfo]:
    aggs: List[AggInfo] = []
    for node in ast.find_all(exp.AggFunc):
        # exp.AggFunc 是 COUNT/SUM/AVG/MIN/MAX 的基类
        func_name = type(node).__name__.lower()
        # 去掉 sqlglot 的前缀，如 "count" / "sum"
        func_name = func_name.replace("count", "count").replace("sum", "sum")
        if func_name not in SUPPORTED_AGGS:
            continue
        # 取出列
        col = "*"
        is_distinct = False
        if isinstance(node, exp.Count):
            for e in node.expressions:
                if isinstance(e, exp.Star):
                    col = "*"
                elif isinstance(e, exp.Column):
                    col = e.name
            # COUNT(DISTINCT x) 检查
            if node.args.get("distinct"):
                is_distinct = True
        elif hasattr(node, "expressions"):
            for e in node.expressions:
                if isinstance(e, exp.Column):
                    col = e.name
        aggs.append(AggInfo(func=func_name, col=col, is_distinct=is_distinct))
    return aggs


# ---------------------------------------------------------------------------
# LIMIT / OFFSET 提取
# ---------------------------------------------------------------------------

def _extract_limit_offset(ast: exp.Select) -> Tuple[Optional[int], Optional[int]]:
    limit_node = ast.args.get("limit")
    offset_node = ast.args.get("offset")

    limit_val: Optional[int] = None
    offset_val: Optional[int] = None

    if limit_node is not None:
        if isinstance(limit_node, exp.Limit):
            expr = limit_node.args.get("expression")
            if isinstance(expr, exp.Literal) and expr.is_int:
                limit_val = int(expr.this)
        elif isinstance(limit_node, exp.Literal) and limit_node.is_int:
            limit_val = int(limit_node.this)

    if offset_node is not None:
        if isinstance(offset_node, exp.Offset):
            expr = offset_node.args.get("expression")
            if isinstance(expr, exp.Literal) and expr.is_int:
                offset_val = int(expr.this)
        elif isinstance(offset_node, exp.Literal) and offset_node.is_int:
            offset_val = int(offset_node.this)

    return limit_val, offset_val


# ---------------------------------------------------------------------------
# ORDER BY 提取
# ---------------------------------------------------------------------------

def _extract_order_by(ast: exp.Select) -> Tuple[bool, List[Tuple[str, bool]]]:
    order = ast.args.get("order")
    if order is None:
        return False, []
    spec: List[Tuple[str, bool]] = []
    for o in order.expressions:
        if isinstance(o, exp.Ordered):
            col_expr = o.args.get("this")
            desc = o.args.get("desc") or False
            if isinstance(col_expr, exp.Column):
                spec.append((col_expr.name, bool(desc)))
            elif isinstance(col_expr, exp.AggFunc):
                # ORDER BY COUNT(*) 这种情况
                spec.append((str(col_expr), bool(desc)))
        elif isinstance(o, exp.Column):
            spec.append((o.name, False))
    return len(spec) > 0, spec


# ---------------------------------------------------------------------------
# 表 / category 提取
# ---------------------------------------------------------------------------

def _extract_tables(ast: exp.Select) -> Set[str]:
    """收集 FROM/JOIN 中出现的表名。"""
    tables: Set[str] = set()
    for t in ast.find_all(exp.Table):
        name = (t.name or "").lower()
        if name:
            tables.add(name)
    # 语义检查
    unknown = tables - ALLOWED_TABLES
    if unknown:
        raise ValueError(f"未知的表: {sorted(unknown)}，允许: {sorted(ALLOWED_TABLES)}")
    return tables


def _has_category_in_select(ast: exp.Select) -> bool:
    for col in ast.find_all(exp.Column):
        if col.name == "category":
            return True
    for star in ast.find_all(exp.Star):
        return True
    return False


def _extract_category_values(ast: exp.Select) -> Set[str]:
    """从 WHERE 条件中提取所有直接比较到的 category 值。"""
    cats: Set[str] = set()
    where = ast.args.get("where")
    if where is None:
        return cats

    for node in where.walk():
        if isinstance(node, exp.EQ):
            left, right = node.left, node.right
            if isinstance(left, exp.Column) and left.name == "category":
                _try_add_literal(right, cats)
            elif isinstance(right, exp.Column) and right.name == "category":
                _try_add_literal(left, cats)
        elif isinstance(node, exp.In):
            col = node.this
            if isinstance(col, exp.Column) and col.name == "category":
                for v in node.expressions:
                    _try_add_literal(v, cats)
    return cats


def _try_add_literal(node: exp.Expression, out: Set[str]) -> None:
    if isinstance(node, exp.Literal) and node.is_string:
        out.add(node.this)


# ---------------------------------------------------------------------------
# 辅助：带 category 条件的子查询构造（给 decomposer 使用）
# ---------------------------------------------------------------------------

def add_category_predicate(ast: exp.Select, category: str) -> exp.Select:
    """返回一个新的 AST，在 WHERE 中追加 `AND category = '<category>'` 条件。"""
    new_ast = ast.copy()
    pred = exp.EQ(
        this=exp.Column(this=exp.to_identifier("category")),
        expression=exp.Literal.string(category),
    )
    where = new_ast.args.get("where")
    if where is None:
        new_ast.set("where", exp.Where(this=pred))
    else:
        new_ast.set(
            "where",
            exp.Where(this=exp.And(this=where.this, expression=pred)),
        )
    return new_ast


def ast_to_sql(ast: exp.Select) -> str:
    """把 AST 转回 SQL 字符串。"""
    return ast.sql(dialect="sqlite")
