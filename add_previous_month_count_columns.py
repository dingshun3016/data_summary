#!/usr/bin/env python3
"""给已有 HTML 看板补充“此前月份数量”列。"""

from __future__ import annotations

import re
from pathlib import Path

import enhance_html_sorting


ROOT = Path(__file__).resolve().parent


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def previous_month_count(cell_html: str) -> int:
    text = strip_tags(cell_html)
    if not text or "未出现" in text:
        return 0
    parts = [part for part in re.split(r"[、,，;；\s]+", text) if part]
    return len(parts)


def split_cells(row_html: str, tag: str) -> list[str]:
    return re.findall(rf"<{tag}\b[^>]*>.*?</{tag}>", row_html, flags=re.S | re.I)


def insert_count_cell(row_html: str, previous_index: int, count: int) -> str:
    cells = split_cells(row_html, "td")
    if len(cells) <= previous_index:
        return row_html
    replacement = f'<td class="number previous-count">{count}</td>{cells[previous_index]}'
    return row_html.replace(cells[previous_index], replacement, 1)


def enhance_table(table_html: str) -> tuple[str, bool]:
    header_match = re.search(r"<thead>\s*<tr>(.*?)</tr>\s*</thead>", table_html, flags=re.S | re.I)
    if not header_match:
        return table_html, False
    header_row = header_match.group(1)
    headers = split_cells(header_row, "th")
    previous_index = None
    for index, header in enumerate(headers):
        label = strip_tags(header)
        if "此前月份数量" in label or "此前出现月份数量" in label:
            return table_html, False
        if "此前" in label and "月份" in label:
            previous_index = index
            break
    if previous_index is None:
        return table_html, False

    new_header = header_row.replace(headers[previous_index], "<th>此前月份数量</th>" + headers[previous_index], 1)
    table_html = table_html.replace(header_row, new_header, 1)

    def replace_row(match: re.Match[str]) -> str:
        row_html = match.group(0)
        cells = split_cells(row_html, "td")
        if len(cells) <= previous_index:
            return row_html
        return insert_count_cell(row_html, previous_index, previous_month_count(cells[previous_index]))

    table_html = re.sub(r"<tr\b[^>]*>.*?</tr>", replace_row, table_html, flags=re.S | re.I)
    return table_html, True


def enhance_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False

    def replace_table(match: re.Match[str]) -> str:
        nonlocal changed
        table_html, table_changed = enhance_table(match.group(0))
        changed = changed or table_changed
        return table_html

    text = re.sub(r"<table\b[^>]*>.*?</table>", replace_table, text, flags=re.S | re.I)
    if changed:
        path.write_text(text, encoding="utf-8")
        enhance_html_sorting.enhance_file(path)
    return changed


def main() -> None:
    files = sorted(ROOT.glob("*_results/*.html"))
    changed = [path for path in files if enhance_file(path)]
    print(f"[扫描] {len(files)} 个结果看板")
    print(f"[补列] {len(changed)} 个 HTML")
    for path in changed:
        print(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
