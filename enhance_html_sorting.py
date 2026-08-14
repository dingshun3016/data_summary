#!/usr/bin/env python3
"""给结果看板 HTML 注入表格排序按钮。

只处理各类 *_results 目录下的 HTML，看板重新生成后可再次运行本脚本。
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STYLE_MARKER = "/* dashboard-sort-enhancer */"
SCRIPT_MARKER = "<!-- dashboard-sort-enhancer -->"

SORT_STYLE = f"""
{STYLE_MARKER}
th.sortable-header {{
  cursor: pointer;
  user-select: none;
}}
th.sortable-header .sort-button {{
  width: 100%;
  min-height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: flex-start;
  gap: 6px;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 0;
  font: inherit;
  font-weight: 700;
  text-align: left;
  cursor: pointer;
}}
th.sortable-header .sort-icon {{
  min-width: 14px;
  color: #dbeafe;
  font-size: 12px;
}}
th.sortable-header.sorted .sort-icon {{
  color: #fff7ad;
}}
"""

SORT_SCRIPT = f"""{SCRIPT_MARKER}
<script>
(function () {{
  const metricWords = ['总市值', '市值', '次数', '涨跌幅', '历史新高次数', '基金数量', '持有该股票的基金数量', '月份数量', '月数', '数量'];
  const excludedWords = ['股票代码', '股票简称', '同花顺行业', '行业', '月份', '此前', '变化'];

  function shouldSort(label) {{
    const text = label.replace(/\\s+/g, '');
    if (!text) return false;
    if (excludedWords.some(word => text.includes(word)) &&
        !text.includes('总市值') &&
        !text.includes('数量') &&
        !text.includes('月数') &&
        !text.includes('次数') &&
        !text.includes('涨跌幅') &&
        !text.includes('历史新高次数')) return false;
    return metricWords.some(word => text.includes(word));
  }}

  function numericValue(text) {{
    const raw = (text || '').replace(/,/g, '').replace(/，/g, '').trim();
    const match = raw.match(/-?\\d+(?:\\.\\d+)?/);
    if (!match) return Number.NEGATIVE_INFINITY;
    let value = Number(match[0]);
    if (!Number.isFinite(value)) return Number.NEGATIVE_INFINITY;
    if (raw.includes('万亿')) value *= 10000;
    else if (raw.includes('万') && !raw.includes('亿')) value /= 10000;
    return value;
  }}

  function refreshRank(table) {{
    const firstHeader = table.querySelector('thead th');
    if (!firstHeader || !/(序号|排名)/.test(firstHeader.innerText)) return;
    Array.from(table.tBodies[0]?.rows || []).forEach((row, index) => {{
      const cell = row.cells[0];
      if (cell && /^\\d+$/.test(cell.innerText.trim())) cell.innerText = String(index + 1);
    }});
  }}

  function enhanceTable(table) {{
    if (table.dataset.sortEnhanced === '1') return;
    const headers = Array.from(table.querySelectorAll('thead th'));
    const tbody = table.tBodies[0];
    if (!headers.length || !tbody) return;
    headers.forEach((th, index) => {{
      const label = th.innerText.trim();
      if (!shouldSort(label)) return;
      th.classList.add('sortable-header');
      th.dataset.sortDir = 'none';
      th.innerHTML = '<button class="sort-button" type="button"><span>' +
        label.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') +
        '</span><span class="sort-icon" aria-hidden="true">↕</span></button>';
      th.querySelector('button').addEventListener('click', () => {{
        const nextDir = th.dataset.sortDir === 'desc' ? 'asc' : 'desc';
        headers.forEach(item => {{
          item.classList.remove('sorted');
          if (item.dataset.sortDir) item.dataset.sortDir = 'none';
          const icon = item.querySelector('.sort-icon');
          if (icon) icon.textContent = '↕';
        }});
        th.dataset.sortDir = nextDir;
        th.classList.add('sorted');
        th.querySelector('.sort-icon').textContent = nextDir === 'desc' ? '↓' : '↑';
        const rows = Array.from(tbody.rows);
        rows.sort((a, b) => {{
          const av = numericValue(a.cells[index]?.innerText || '');
          const bv = numericValue(b.cells[index]?.innerText || '');
          return nextDir === 'desc' ? bv - av : av - bv;
        }});
        rows.forEach(row => tbody.appendChild(row));
        refreshRank(table);
      }});
    }});
    table.dataset.sortEnhanced = '1';
  }}

  document.querySelectorAll('table').forEach(enhanceTable);
}})();
</script>
"""


def result_html_files() -> list[Path]:
    return sorted(
        path
        for path in ROOT.glob("*_results/*.html")
        if path.is_file()
    )


def enhance_file(path: Path | str) -> bool:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    changed = False
    if STYLE_MARKER not in text:
        if "</style>" in text:
            text = text.replace("</style>", SORT_STYLE + "\n</style>", 1)
        else:
            text = text.replace("</head>", f"<style>{SORT_STYLE}</style></head>", 1)
        changed = True
    else:
        text, count = re.subn(
            rf"\n?{re.escape(STYLE_MARKER)}.*?(?=</style>)",
            lambda _: SORT_STYLE + "\n",
            text,
            count=1,
            flags=re.S,
        )
        changed = changed or bool(count)
    if SCRIPT_MARKER not in text:
        text = text.replace("</body>", SORT_SCRIPT + "\n</body>", 1)
        changed = True
    else:
        text, count = re.subn(
            rf"{re.escape(SCRIPT_MARKER)}\s*<script>.*?</script>",
            lambda _: SORT_SCRIPT,
            text,
            count=1,
            flags=re.S,
        )
        changed = changed or bool(count)
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> None:
    files = result_html_files()
    changed = [path for path in files if enhance_file(path)]
    print(f"[扫描] {len(files)} 个结果看板")
    print(f"[更新] {len(changed)} 个 HTML")
    for path in changed:
        print(f"  {path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
