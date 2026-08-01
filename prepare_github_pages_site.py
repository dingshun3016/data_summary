#!/usr/bin/env python3
"""准备 GitHub Pages 静态发布目录。"""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SITE_DIR = ROOT / "github_pages_site"
NAV_FILE = ROOT / "数据看板导航.html"
RESULT_DIRS = [
    "five_star_fund_holdings_results",
    "four_star_fund_holdings_results",
    "three_star_fund_holdings_results",
    "monthly_daily_gain_over_7_count_results",
    "monthly_gain_over_20_results",
    "monthly_history_high_count_results",
    "monthly_limit_up_count_over_1_results",
    "monthly_near_1y_high_results",
]


def copy_tree(source: Path, target: Path) -> int:
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return sum(1 for path in target.rglob("*") if path.is_file())


def main() -> None:
    SITE_DIR.mkdir(exist_ok=True)
    copied = 0

    if not NAV_FILE.exists():
        raise FileNotFoundError(f"导航页不存在：{NAV_FILE}")
    shutil.copy2(NAV_FILE, SITE_DIR / "数据看板导航.html")
    shutil.copy2(NAV_FILE, SITE_DIR / "index.html")
    copied += 2

    for dirname in RESULT_DIRS:
        source = ROOT / dirname
        if not source.exists():
            continue
        copied += copy_tree(source, SITE_DIR / dirname)

    (SITE_DIR / ".nojekyll").write_text("", encoding="utf-8")
    (SITE_DIR / "README.md").write_text(
        "# 数据看板导航\n\n"
        "这是由本地股票/基金看板生成的 GitHub Pages 静态站点。\n\n"
        "入口：`index.html`\n",
        encoding="utf-8",
    )

    print(f"[完成] {SITE_DIR}")
    print(f"[文件数] {copied}")
    print("[入口] index.html")


if __name__ == "__main__":
    main()
