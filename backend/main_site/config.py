"""分站点配置。

定义每个分站点的 id、URL、负责的分类。
主站点据此判断查询涉及哪些分站点、并构建子查询。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List
import yaml
from pathlib import Path


@dataclass
class SiteConfig:
    id: str
    url: str
    categories: List[str] = field(default_factory=list)


# 数据模型：books 表被 books.category 水平划分
# readers 表在每个分站点都有完整副本，不参与划分
PARTITION_TABLE = "books"
PARTITION_COLUMN = "category"

# 不参与划分的表在所有站点都有完整副本
REPLICATED_TABLES = {"readers"}


def load_sites() -> List[SiteConfig]:
    """加载 config.yaml 中的分站点列表；若文件不存在则使用硬编码默认值。"""
    cfg_path = Path(__file__).parent / "config.yaml"
    if cfg_path.exists():
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return [SiteConfig(**s) for s in data.get("sites", [])]

    # 默认配置（与产品文档保持一致）
    return [
        SiteConfig("Site A", "http://localhost:8001", ["文学"]),
        SiteConfig("Site B", "http://localhost:8002", ["科技"]),
        SiteConfig("Site C", "http://localhost:8003", ["教育"]),
        SiteConfig("Site D", "http://localhost:8004", ["历史"]),
    ]


SITES: List[SiteConfig] = load_sites()


def category_to_site(category: str) -> SiteConfig | None:
    """根据 category 值找到对应的分站点。"""
    for s in SITES:
        if category in s.categories:
            return s
    return None


def sites_for_categories(categories: List[str]) -> List[SiteConfig]:
    """根据一组 category 值找到涉及的分站点（去重保序）。"""
    seen_ids = set()
    result: List[SiteConfig] = []
    for cat in categories:
        site = category_to_site(cat)
        if site and site.id not in seen_ids:
            result.append(site)
            seen_ids.add(site.id)
    return result
