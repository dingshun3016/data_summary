#!/usr/bin/python
# -*- coding: utf-8 -*-
"""清理同一结果目录中的旧 HTML，只保留本次生成的网页。"""

from pathlib import Path


def remove_old_html(current_html):
    current = Path(current_html).resolve()
    if not current.is_file():
        raise FileNotFoundError(f"新网页不存在，取消清理旧网页：{current}")

    removed = []
    for candidate in current.parent.glob("*.html"):
        if candidate.resolve() == current:
            continue
        candidate.unlink()
        removed.append(candidate.name)

    if removed:
        print(f"[清理旧网页] 已删除 {len(removed)} 个：{', '.join(sorted(removed))}")
    else:
        print("[清理旧网页] 没有需要删除的旧网页")
    return removed


def finalize_html(current_html):
    """保留新网页、删除同目录旧网页，并刷新统一导航。"""
    removed = remove_old_html(current_html)
    import generate_dashboard_index

    generate_dashboard_index.main()
    return removed
