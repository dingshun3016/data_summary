#!/usr/bin/env python3
"""扫描当前目录下的结果页，生成统一的数据看板导航。"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "数据看板导航.html"

CATEGORIES = [
    (
        "市场筛选看板",
        "market",
        [
            (
                "monthly_gain_over_20_results",
                "月度涨幅大于20%",
                "按指定年月范围汇总涨幅超过20%的股票，并对比前期出现情况。",
                "monthly_gain_over_20_dashboard.py",
            ),
            (
                "monthly_limit_up_count_over_1_results",
                "月度涨停次数大于1",
                "按指定年月范围汇总涨停次数大于1的股票，并对比前期出现情况。",
                "monthly_limit_up_count_over_1_dashboard.py",
            ),
            (
                "monthly_daily_gain_over_7_count_results",
                "月度单日涨幅大于7%次数",
                "按指定年月范围汇总单日涨幅大于7%的出现次数和股票明细。",
                "monthly_daily_gain_over_7_count_dashboard.py",
            ),
            (
                "monthly_near_1y_high_results",
                "月度近1年新高",
                "汇总各月出现近1年新高的股票。",
                "monthly_near_1y_high_dashboard.py",
            ),
            (
                "monthly_history_high_count_results",
                "月度历史新高次数",
                "汇总各月股票出现历史新高的次数。",
                "monthly_history_high_count_dashboard.py",
            ),
        ],
    ),
    (
        "基金持仓看板",
        "fund",
        [
            (
                "five_star_fund_holdings_results",
                "五星基金持仓",
                "按季度和年度汇总五星基金持仓、排名及排名变化。",
                "five_star_fund_quarterly_holdings.py",
            ),
            (
                "four_star_fund_holdings_results",
                "四星基金持仓",
                "按季度和年度汇总四星基金持仓、排名及排名变化。",
                "four_star_fund_quarterly_holdings.py",
            ),
            (
                "three_star_fund_holdings_results",
                "三星基金持仓",
                "按季度和年度汇总三星基金持仓、排名及排名变化。",
                "three_star_fund_quarterly_holdings.py",
            ),
        ],
    ),
]


def format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def collect_dashboards() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    dashboards: list[dict[str, str]] = []
    missing: list[dict[str, str]] = []

    for category_name, category_key, definitions in CATEGORIES:
        for directory, title, description, script in definitions:
            result_dir = ROOT / directory
            pages = sorted(
                result_dir.glob("*.html") if result_dir.exists() else [],
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if not pages:
                missing.append(
                    {
                        "category": category_name,
                        "category_key": category_key,
                        "title": title,
                        "description": description,
                        "script": script,
                    }
                )
                continue

            for index, page in enumerate(pages):
                stat = page.stat()
                dashboards.append(
                    {
                        "category": category_name,
                        "category_key": category_key,
                        "title": title,
                        "description": description,
                        "filename": page.stem,
                        "href": page.relative_to(ROOT).as_posix(),
                        "size": format_size(stat.st_size),
                        "updated": datetime.fromtimestamp(stat.st_mtime).strftime(
                            "%Y-%m-%d %H:%M"
                        ),
                        "latest": "最新" if index == 0 else "历史",
                    }
                )

    return dashboards, missing


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def dashboard_row(item: dict[str, str]) -> str:
    search = " ".join(item.values())
    return f"""
          <article class="dashboard-row" data-category="{escape(item['category_key'])}" data-search="{escape(search.lower())}">
            <div class="dashboard-main">
              <div class="title-line">
                <h3>{escape(item['title'])}</h3>
                <span class="badge">{escape(item['latest'])}</span>
              </div>
              <p>{escape(item['filename'])}</p>
              <div class="meta">
                <span>{escape(item['updated'])}</span>
                <span>{escape(item['size'])}</span>
              </div>
            </div>
            <a class="open-link" href="{escape(item['href'])}" target="_blank" rel="noopener">打开看板 <span aria-hidden="true">↗</span></a>
          </article>"""


def missing_row(item: dict[str, str]) -> str:
    search = " ".join(item.values())
    return f"""
          <article class="dashboard-row missing" data-category="{escape(item['category_key'])}" data-search="{escape(search.lower())}">
            <div class="dashboard-main">
              <div class="title-line">
                <h3>{escape(item['title'])}</h3>
                <span class="badge muted">暂无看板</span>
              </div>
              <p>{escape(item['description'])}</p>
              <div class="meta"><span>生成脚本：{escape(item['script'])}</span></div>
            </div>
            <span class="unavailable">等待生成</span>
          </article>"""


def render() -> str:
    dashboards, missing = collect_dashboards()
    categories_with_pages = len({item["category_key"] for item in dashboards})
    latest_update = max(
        (item["updated"] for item in dashboards),
        default="暂无数据",
    )

    sections = []
    for category_name, category_key, _ in CATEGORIES:
        rows = [
            dashboard_row(item)
            for item in dashboards
            if item["category_key"] == category_key
        ]
        rows.extend(
            missing_row(item)
            for item in missing
            if item["category_key"] == category_key
        )
        sections.append(
            f"""
      <section class="dashboard-section" data-section="{category_key}">
        <div class="section-heading">
          <h2>{escape(category_name)}</h2>
          <span>{sum(1 for item in dashboards if item['category_key'] == category_key)} 个可用页面</span>
        </div>
        <div class="dashboard-list">
          {''.join(rows)}
        </div>
      </section>"""
        )

    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>数据看板导航</title>
  <style>
    :root {{
      color-scheme: light;
      --ink: #172033;
      --muted: #64748b;
      --line: #dbe3ee;
      --soft: #f4f7fb;
      --accent: #1769aa;
      --accent-dark: #0f4f82;
      --green: #147d64;
      --white: #ffffff;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--soft);
      color: var(--ink);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
    }}
    header {{
      background: #123b5d;
      color: var(--white);
      border-bottom: 4px solid #28a07d;
    }}
    .header-inner, main, footer {{
      width: min(1180px, calc(100% - 32px));
      margin: 0 auto;
    }}
    .header-inner {{
      padding: 28px 0 24px;
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 24px;
    }}
    h1 {{ margin: 0 0 6px; font-size: 28px; letter-spacing: 0; }}
    .subtitle {{ margin: 0; color: #c9d9e7; font-size: 14px; }}
    .stats {{
      display: grid;
      grid-template-columns: repeat(3, minmax(112px, 1fr));
      gap: 1px;
      background: rgba(255,255,255,.18);
      border: 1px solid rgba(255,255,255,.18);
    }}
    .stat {{ padding: 10px 14px; background: rgba(18,59,93,.9); }}
    .stat strong {{ display: block; font-size: 18px; }}
    .stat span {{ color: #c9d9e7; font-size: 12px; }}
    main {{ padding: 24px 0 40px; }}
    .toolbar {{
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 26px;
    }}
    .search-wrap {{ position: relative; flex: 1; min-width: 220px; }}
    .search-wrap span {{
      position: absolute;
      left: 13px;
      top: 50%;
      transform: translateY(-50%);
      color: var(--muted);
      font-size: 17px;
    }}
    input {{
      width: 100%;
      height: 42px;
      padding: 0 14px 0 39px;
      border: 1px solid #c9d4e2;
      border-radius: 6px;
      background: var(--white);
      color: var(--ink);
      font-size: 14px;
      outline: none;
    }}
    input:focus {{ border-color: var(--accent); box-shadow: 0 0 0 3px rgba(23,105,170,.12); }}
    .filters {{ display: flex; gap: 2px; }}
    button {{
      height: 42px;
      border: 1px solid #c9d4e2;
      background: var(--white);
      color: #42526a;
      padding: 0 14px;
      cursor: pointer;
      font-size: 13px;
    }}
    button:first-child {{ border-radius: 6px 0 0 6px; }}
    button:last-child {{ border-radius: 0 6px 6px 0; }}
    button.active {{ background: var(--accent); border-color: var(--accent); color: var(--white); }}
    .dashboard-section {{ margin-bottom: 30px; }}
    .section-heading {{
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 10px;
    }}
    .section-heading h2 {{ margin: 0; font-size: 19px; letter-spacing: 0; }}
    .section-heading span {{ color: var(--muted); font-size: 12px; }}
    .dashboard-list {{ border-top: 1px solid var(--line); }}
    .dashboard-row {{
      min-height: 104px;
      padding: 17px 18px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 24px;
      background: var(--white);
      border: 1px solid var(--line);
      border-top: 0;
    }}
    .dashboard-row:hover {{ background: #fafdff; }}
    .dashboard-row[hidden], .dashboard-section[hidden] {{ display: none; }}
    .dashboard-main {{ min-width: 0; }}
    .title-line {{ display: flex; align-items: center; gap: 9px; }}
    h3 {{ margin: 0; font-size: 16px; letter-spacing: 0; }}
    .dashboard-main p {{
      margin: 7px 0 8px;
      color: #42526a;
      font-size: 13px;
      overflow-wrap: anywhere;
    }}
    .meta {{ display: flex; gap: 16px; color: var(--muted); font-size: 12px; }}
    .badge {{
      padding: 2px 6px;
      border-radius: 3px;
      color: var(--green);
      background: #e6f5f0;
      font-size: 11px;
      white-space: nowrap;
    }}
    .badge.muted {{ color: #697386; background: #eef1f5; }}
    .open-link {{
      flex: none;
      min-width: 96px;
      height: 36px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 5px;
      border-radius: 5px;
      background: var(--accent);
      color: var(--white);
      text-decoration: none;
      font-size: 13px;
    }}
    .open-link:hover {{ background: var(--accent-dark); }}
    .unavailable {{ flex: none; color: #8994a5; font-size: 13px; }}
    .empty {{
      display: none;
      padding: 42px 16px;
      text-align: center;
      color: var(--muted);
      border: 1px dashed #bdc9d8;
      background: var(--white);
    }}
    footer {{
      padding: 18px 0 28px;
      color: var(--muted);
      border-top: 1px solid var(--line);
      font-size: 12px;
    }}
    code {{ color: #334155; }}
    @media (max-width: 760px) {{
      .header-inner {{ align-items: stretch; flex-direction: column; }}
      .stats {{ grid-template-columns: 1fr 1fr; }}
      .stat:last-child {{ grid-column: 1 / -1; }}
      .toolbar {{ align-items: stretch; flex-direction: column; }}
      .filters {{ display: grid; grid-template-columns: repeat(3, 1fr); }}
      button, button:first-child, button:last-child {{ border-radius: 5px; }}
      .dashboard-row {{ align-items: stretch; flex-direction: column; gap: 14px; }}
      .open-link {{ width: 100%; }}
      .meta {{ flex-wrap: wrap; gap: 7px 14px; }}
    }}
  </style>
</head>
<body>
  <header>
    <div class="header-inner">
      <div>
        <h1>数据看板导航</h1>
        <p class="subtitle">集中查看股票筛选与基金持仓汇总结果</p>
      </div>
      <div class="stats">
        <div class="stat"><strong id="visible-count">{len(dashboards)}</strong><span>可用页面</span></div>
        <div class="stat"><strong>{categories_with_pages}</strong><span>数据类别</span></div>
        <div class="stat"><strong>{escape(latest_update)}</strong><span>最近更新</span></div>
      </div>
    </div>
  </header>
  <main>
    <div class="toolbar">
      <label class="search-wrap">
        <span aria-hidden="true">⌕</span>
        <input id="search" type="search" placeholder="搜索看板、时间范围或文件名" autocomplete="off">
      </label>
      <div class="filters" aria-label="看板分类">
        <button class="active" type="button" data-filter="all">全部</button>
        <button type="button" data-filter="market">市场筛选</button>
        <button type="button" data-filter="fund">基金持仓</button>
      </div>
    </div>
    {''.join(sections)}
    <div class="empty" id="empty">没有找到匹配的看板</div>
  </main>
  <footer>
    生成时间：{escape(generated_at)}。新增结果页后运行 <code>python3 generate_dashboard_index.py</code> 即可更新导航。
  </footer>
  <script>
    const search = document.querySelector('#search');
    const rows = [...document.querySelectorAll('.dashboard-row')];
    const sections = [...document.querySelectorAll('.dashboard-section')];
    const buttons = [...document.querySelectorAll('[data-filter]')];
    const visibleCount = document.querySelector('#visible-count');
    const empty = document.querySelector('#empty');
    let activeFilter = 'all';

    function applyFilters() {{
      const keyword = search.value.trim().toLowerCase();
      let availableVisible = 0;
      let anyVisible = false;

      rows.forEach(row => {{
        const categoryMatches = activeFilter === 'all' || row.dataset.category === activeFilter;
        const keywordMatches = !keyword || row.dataset.search.includes(keyword);
        const show = categoryMatches && keywordMatches;
        row.hidden = !show;
        if (show) {{
          anyVisible = true;
          if (!row.classList.contains('missing')) availableVisible += 1;
        }}
      }});

      sections.forEach(section => {{
        section.hidden = ![...section.querySelectorAll('.dashboard-row')].some(row => !row.hidden);
      }});
      visibleCount.textContent = availableVisible;
      empty.style.display = anyVisible ? 'none' : 'block';
    }}

    buttons.forEach(button => button.addEventListener('click', () => {{
      activeFilter = button.dataset.filter;
      buttons.forEach(item => item.classList.toggle('active', item === button));
      applyFilters();
    }}));
    search.addEventListener('input', applyFilters);
  </script>
</body>
</html>
"""


def main() -> None:
    OUTPUT.write_text(render(), encoding="utf-8")
    dashboards, missing = collect_dashboards()
    print(f"[完成] {OUTPUT}")
    print(f"[可用看板] {len(dashboards)} 个；[尚未生成] {len(missing)} 个")


if __name__ == "__main__":
    main()
