"""SQL 解析与语义分析。

使用 sqlglot 将用户 SQL 解析为 AST，提取：
- 涉及的表
- WHERE 条件中出现的 category 值（用于判断涉及哪些分站点）
- 选择列 / JOIN / ORDER BY 等信息
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


# 允许的表（用于语义检查）
ALLOWED_TABLES = {"books", "borrow_records", "readers"}


@dataclass
class ParsedQuery:
    """解析后的查询信息。"""

    original_sql: str
    ast: exp.Select
    tables: Set[str] = field(default_factory=set)
    categories_in_where: Set[str] = field(default_factory=set)
    has_category_column: bool = False  # SELECT 中是否包含 category

    def sites_categories(self) -> Optional[List[str]]:
        """返回 WHERE 条件中明确限定的 category 值列表；若无则返回 None。"""
        if not self.categories_in_where:
            return None
        return sorted(self.categories_in_where)


def parse_sql(sql: str) -> ParsedQuery:
    """解析 SQL 并进行语义分析。

    仅支持单条 SELECT；遇到不支持的结构（嵌套子查询、GROUP BY 等）会抛出 ValueError。
    """
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

    return ParsedQuery(
        original_sql=sql,
        ast=ast,
        tables=tables,
        categories_in_where=cats,
        has_category_column=has_cat,
    )


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------

def _validate(ast: exp.Select) -> None:
    """拒绝不支持的特性。"""
    # 不支持嵌套子查询
    if ast.find(exp.Subquery):
        raise ValueError("不支持嵌套子查询")

    if ast.args.get("group"):
        raise ValueError("不支持 GROUP BY / 聚合函数")
    if ast.args.get("having"):
        raise ValueError("不支持 HAVING")
    if ast.args.get("limit") or ast.args.get("offset"):
        raise ValueError("不支持 LIMIT / OFFSET")
    if ast.args.get("distinct"):
        raise ValueError("不支持 DISTINCT")


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
        # SELECT * 包含 category
        return True
    return False


# ---------------------------------------------------------------------------
# category 值提取（用于判断涉及哪些分站点）
# ---------------------------------------------------------------------------

def _extract_category_values(ast: exp.Select) -> Set[str]:
    """从 WHERE 条件中提取所有直接比较到的 category 值。

    仅识别：
        category = '文学'
        category IN ('文学', '科技', ...)
    其它情况（LIKE / OR 混合 / BETWEEN 等）视为"未明确限定"，返回空集。
    """
    cats: Set[str] = set()
    where = ast.args.get("where")
    if where is None:
        return cats

    for node in where.walk():
        # category = 'xxx'  或  'xxx' = category
        if isinstance(node, exp.EQ):
            left, right = node.left, node.right
            if isinstance(left, exp.Column) and left.name == "category":
                _try_add_literal(right, cats)
            elif isinstance(right, exp.Column) and right.name == "category":
                _try_add_literal(left, cats)
        # category IN ('a', 'b', ...)
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
    """返回一个新的 AST，在 WHERE 中追加 `AND category = '<category>'` 条件。

    原有 WHERE 条件保持不变，避免了字符串拼接导致的 SQL 注入风险。
    """
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
