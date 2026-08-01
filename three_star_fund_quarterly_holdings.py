#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
汇总所有三星级偏股/混合基金在任意季度区间内的股票持仓。

总市值 = 所有三星级基金对该股票的持仓市值合计。
基金数量按基金组合去重，同一基金的 A/C 等份额不重复计算。
"""

import argparse
import csv
import html
import json
import os
import random
import re
import time
from collections import defaultdict
from datetime import datetime

import requests
from requests.exceptions import RequestException

import enhance_html_sorting
import html_output_cleanup


FUND_LIST_URL = (
    "http://fund.eastmoney.com/data/FundGuideapi.aspx?"
    "dt=4&ft=hh,gp&sd=&ed=&rt=sz,3_zs,3_ja,3&sc=rt_ja&st=desc"
    "&pi=1&pn=8000&zf=diy&sh=list"
)
HOLDINGS_URL = (
    "http://fundf10.eastmoney.com/FundArchivesDatas.aspx?"
    "type=jjcc&code={code}&topline=10&year={year}&month="
)

CACHE_DIR = "three_star_fund_holdings_cache"
OUTPUT_DIR = "three_star_fund_holdings_results"
FUND_CACHE = os.path.join(CACHE_DIR, "three_star_funds.json")
CACHE_VERSION = 2

SESSION = requests.Session()
SESSION.trust_env = False


def parse_args():
    parser = argparse.ArgumentParser(description="汇总三星级基金季度股票持仓")
    parser.add_argument("--start", required=True, help="起始季度，例如 2025-Q1")
    parser.add_argument("--end", required=True, help="结束季度，例如 2026-Q1")
    parser.add_argument("--refresh-funds", action="store_true", help="刷新当前三星级基金名单")
    parser.add_argument("--refresh-holdings", action="store_true", help="强制重新抓取区间涉及的持仓")
    parser.add_argument(
        "--annual-compare-prior-year",
        action="store_true",
        help="为区间内首个年度额外抓取前一年，用于计算较上年度排名变化",
    )
    parser.add_argument("--dry-run", action="store_true", help="仅显示缓存与抓取计划")
    return parser.parse_args()


def parse_quarter(value):
    match = re.fullmatch(r"(\d{4})-?[Qq]([1-4])", value.strip())
    if not match:
        raise ValueError(f"季度格式必须为 YYYY-Q1 至 YYYY-Q4：{value}")
    return int(match.group(1)), int(match.group(2))


def quarter_range(start, end):
    if start > end:
        raise ValueError(f"起始季度不能晚于结束季度：{start} > {end}")
    result = []
    year, quarter = start
    while (year, quarter) <= end:
        result.append((year, quarter))
        quarter += 1
        if quarter == 5:
            year += 1
            quarter = 1
    return result


def quarter_key(year, quarter):
    return f"{year}-Q{quarter}"


def quarter_label(year, quarter):
    return f"{year}年第{quarter}季度"


def previous_quarter(year, quarter):
    if quarter == 1:
        return year - 1, 4
    return year, quarter - 1


def headers():
    return {
        "User-Agent": random.choice(
            [
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/125 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/125 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Gecko/20100101 Firefox/126",
            ]
        ),
        "Accept": "*/*",
        "Connection": "close",
        "Referer": "http://fund.eastmoney.com/",
    }


def request_text(url, retry=3, timeout=20):
    last_error = None
    for attempt in range(retry):
        try:
            response = SESSION.get(url, headers=headers(), timeout=timeout)
            response.raise_for_status()
            return response.content.decode("utf-8", "ignore")
        except RequestException as exc:
            last_error = exc
            if attempt < retry - 1:
                time.sleep(1 + attempt)
    raise RuntimeError(f"请求失败：{url}；原因：{last_error}")


def normalize_fund_name(name):
    """将同一组合的 A/C/E 等基金份额合并，避免重复计算持仓。"""
    return re.sub(
        r"(人民币|美元现汇|美元现钞|A|B|C|D|E|H|I|Y)$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    )


def load_three_star_funds(refresh=False):
    if not refresh and os.path.exists(FUND_CACHE):
        with open(FUND_CACHE, encoding="utf-8") as file:
            payload = json.load(file)
        print(f"[使用基金名单缓存] {len(payload['funds'])} 个基金组合")
        return payload

    text = request_text(FUND_LIST_URL)
    json_text = text[text.find("=") + 1 :].strip().rstrip(";")
    data = json.loads(json_text)
    portfolios = {}
    for row in data.get("datas", []):
        fields = row.split(",")
        if len(fields) < 4:
            continue
        code, name, fund_type = fields[0], fields[1], fields[3]
        if fund_type.startswith("QDII"):
            continue
        base_name = normalize_fund_name(name)
        portfolios.setdefault(base_name, {"code": code, "name": name, "base_name": base_name})

    payload = {
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "funds": sorted(portfolios.values(), key=lambda item: item["code"]),
    }
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(FUND_CACHE, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    print(f"[刷新基金名单] {len(payload['funds'])} 个基金组合")
    return payload


def fund_year_cache_path(code, year):
    return os.path.join(CACHE_DIR, "holdings", code, f"{year}.json")


def parse_fund_year_holdings(text, fund):
    raw = text[text.find("=") + 1 :].strip().rstrip(";")
    raw = raw.replace("content:", '"content":', 1)
    raw = raw.replace("arryear:", '"arryear":', 1)
    raw = raw.replace("curyear:", '"curyear":', 1)
    data = json.loads(raw)
    content = data.get("content", "")
    result = {}

    blocks = re.findall(
        r"<h4[^>]*>(.*?)</h4>.*?<tbody>(.*?)</tbody>",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for title_html, tbody_html in blocks:
        title_text = strip_html(title_html)
        match = re.search(r"(\d{4})年(\d)季度股票投资明细", title_text)
        if not match:
            continue
        key = quarter_key(int(match.group(1)), int(match.group(2)))
        holdings = []
        for row_html in re.findall(
            r"<tr[^>]*>(.*?)</tr>", tbody_html, flags=re.IGNORECASE | re.DOTALL
        ):
            cells = [
                strip_html(cell)
                for cell in re.findall(
                    r"<td[^>]*>(.*?)</td>",
                    row_html,
                    flags=re.IGNORECASE | re.DOTALL,
                )
            ]
            if len(cells) < 7:
                continue
            code = cells[1]
            if not re.fullmatch(r"\d{6}", code):
                continue
            # 东方财富不同年份的表格列数可能不同，持仓市值始终为最后一列。
            market_value_text = cells[-1].replace(",", "")
            try:
                market_value_wan = float(market_value_text)
            except ValueError:
                continue
            holdings.append(
                {
                    "stock_code": code,
                    "stock_name": cells[2],
                    "market_value_wan": market_value_wan,
                }
            )
        result[key] = holdings

    return {
        "cache_version": CACHE_VERSION,
        "fund": fund,
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "quarters": result,
    }


def strip_html(value):
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return re.sub(r"\s+", " ", html.unescape(value)).strip()


def load_fund_year(fund, year, refresh=False):
    path = fund_year_cache_path(fund["code"], year)
    if not refresh and os.path.exists(path):
        with open(path, encoding="utf-8") as file:
            cached = json.load(file)
        # 旧解析器可能因页面列结构变化留下全空缓存，自动重新抓取。
        if cached.get("cache_version") == CACHE_VERSION or any(
            cached.get("quarters", {}).values()
        ):
            return cached, True

    text = request_text(HOLDINGS_URL.format(code=fund["code"], year=year))
    payload = parse_fund_year_holdings(text, fund)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    time.sleep(0.15)
    return payload, False


def aggregate_quarters(funds, quarters, refresh=False, dry_run=False):
    years = sorted({year for year, _ in quarters})
    needed = []
    cached = 0
    for fund in funds:
        for year in years:
            path = fund_year_cache_path(fund["code"], year)
            valid_cache = False
            if os.path.exists(path) and not refresh:
                with open(path, encoding="utf-8") as file:
                    cached_payload = json.load(file)
                valid_cache = (
                    cached_payload.get("cache_version") == CACHE_VERSION
                    or any(cached_payload.get("quarters", {}).values())
                )
            if valid_cache:
                cached += 1
            else:
                needed.append((fund, year))

    print(f"[基金组合] {len(funds)} 个")
    print(f"[涉及持仓年份] {', '.join(str(year) for year in years)}")
    print(f"[基金年份缓存] 已有 {cached} 个；需要抓取 {len(needed)} 个")
    if dry_run:
        return {}

    fund_year_data = {}
    total = len(funds) * len(years)
    done = 0
    for fund in funds:
        for year in years:
            payload, used_cache = load_fund_year(fund, year, refresh)
            fund_year_data[(fund["code"], year)] = payload
            done += 1
            print(
                f"  [{done}/{total}] {fund['name']} {year}"
                f"{' 使用缓存' if used_cache else ' 抓取完成'}"
            )

    summaries = {}
    for year, quarter in quarters:
        key = quarter_key(year, quarter)
        stocks = defaultdict(lambda: {"stock_name": "", "total_market_value_wan": 0.0, "fund_codes": set()})
        reporting_funds = 0
        for fund in funds:
            holdings = fund_year_data[(fund["code"], year)]["quarters"].get(key, [])
            if holdings:
                reporting_funds += 1
            for holding in holdings:
                stock = stocks[holding["stock_code"]]
                stock["stock_name"] = holding["stock_name"]
                stock["total_market_value_wan"] += holding["market_value_wan"]
                stock["fund_codes"].add(fund["code"])

        rows = [
            {
                "stock_code": code,
                "stock_name": item["stock_name"],
                "total_market_value_wan": round(item["total_market_value_wan"], 2),
                "fund_count": len(item["fund_codes"]),
            }
            for code, item in stocks.items()
        ]
        rows.sort(key=lambda item: (item["fund_count"], item["total_market_value_wan"]), reverse=True)
        summaries[key] = {"rows": rows, "reporting_funds": reporting_funds}
    return summaries


def add_rank_changes(summaries, display_quarters):
    """为展示季度增加排名和相对上一季度的排名变化。"""
    for quarter in display_quarters:
        current_key = quarter_key(*quarter)
        previous_key = quarter_key(*previous_quarter(*quarter))
        previous_rows = summaries.get(previous_key, {}).get("rows", [])
        previous_ranks = {
            row["stock_code"]: index
            for index, row in enumerate(previous_rows, start=1)
        }

        for rank, row in enumerate(summaries[current_key]["rows"], start=1):
            row["rank"] = rank
            previous_rank = previous_ranks.get(row["stock_code"])
            if previous_rank is None:
                row["rank_change"] = "新进" if previous_rows else "无对比"
            else:
                difference = previous_rank - rank
                if difference > 0:
                    row["rank_change"] = f"↑{difference}"
                elif difference < 0:
                    row["rank_change"] = f"↓{-difference}"
                else:
                    row["rank_change"] = "持平"


def build_annual_summaries(quarter_summaries, years):
    """将每个年度四个季度的汇总数据相加。"""
    annual_summaries = {}
    for year in years:
        stocks = defaultdict(
            lambda: {
                "stock_name": "",
                "total_market_value_wan": 0.0,
                "fund_count": 0,
            }
        )
        reporting_funds_sum = 0
        for quarter in range(1, 5):
            summary = quarter_summaries[quarter_key(year, quarter)]
            reporting_funds_sum += summary["reporting_funds"]
            for row in summary["rows"]:
                stock = stocks[row["stock_code"]]
                stock["stock_name"] = row["stock_name"]
                stock["total_market_value_wan"] += row["total_market_value_wan"]
                stock["fund_count"] += row["fund_count"]

        rows = [
            {
                "stock_code": code,
                "stock_name": item["stock_name"],
                "total_market_value_wan": round(item["total_market_value_wan"], 2),
                "fund_count": item["fund_count"],
            }
            for code, item in stocks.items()
        ]
        rows.sort(
            key=lambda item: (item["fund_count"], item["total_market_value_wan"]),
            reverse=True,
        )
        annual_summaries[year] = {
            "rows": rows,
            "reporting_funds_sum": reporting_funds_sum,
        }
    return annual_summaries


def add_annual_rank_changes(annual_summaries, display_years):
    for year in display_years:
        previous_rows = annual_summaries.get(year - 1, {}).get("rows", [])
        previous_ranks = {
            row["stock_code"]: rank
            for rank, row in enumerate(previous_rows, start=1)
        }
        for rank, row in enumerate(annual_summaries[year]["rows"], start=1):
            row["rank"] = rank
            previous_rank = previous_ranks.get(row["stock_code"])
            if previous_rank is None:
                row["rank_change"] = "新进" if previous_rows else "无对比"
            else:
                difference = previous_rank - rank
                if difference > 0:
                    row["rank_change"] = f"↑{difference}"
                elif difference < 0:
                    row["rank_change"] = f"↓{-difference}"
                else:
                    row["rank_change"] = "持平"


def format_yi(value_wan):
    return f"{value_wan / 10000:,.2f}亿"


def save_csvs(quarters, summaries):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for year, quarter in quarters:
        key = quarter_key(year, quarter)
        path = os.path.join(OUTPUT_DIR, f"{key}_三星基金持仓汇总.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "排名",
                    "较上季度排名变化",
                    "股票代码",
                    "股票简称",
                    "总市值",
                    "持有该股票的基金数量",
                ],
            )
            writer.writeheader()
            for row in summaries[key]["rows"]:
                writer.writerow(
                    {
                        "排名": row["rank"],
                        "较上季度排名变化": row["rank_change"],
                        "股票代码": row["stock_code"],
                        "股票简称": row["stock_name"],
                        "总市值": format_yi(row["total_market_value_wan"]),
                        "持有该股票的基金数量": row["fund_count"],
                    }
                )


def save_annual_csvs(years, annual_summaries):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    for year in years:
        path = os.path.join(OUTPUT_DIR, f"{year}_三星基金年度持仓汇总.csv")
        with open(path, "w", newline="", encoding="utf-8-sig") as file:
            writer = csv.DictWriter(
                file,
                fieldnames=[
                    "排名",
                    "较上年度排名变化",
                    "股票代码",
                    "股票简称",
                    "总市值",
                    "持有该股票的基金数量",
                ],
            )
            writer.writeheader()
            for row in annual_summaries[year]["rows"]:
                writer.writerow(
                    {
                        "排名": row["rank"],
                        "较上年度排名变化": row["rank_change"],
                        "股票代码": row["stock_code"],
                        "股票简称": row["stock_name"],
                        "总市值": format_yi(row["total_market_value_wan"]),
                        "持有该股票的基金数量": row["fund_count"],
                    }
                )


def render_rows(rows):
    rendered = []
    for row in rows:
        search = (row["stock_code"] + row["stock_name"]).lower()
        change_class = (
            "up"
            if row["rank_change"].startswith("↑")
            else "down"
            if row["rank_change"].startswith("↓")
            else "new"
            if row["rank_change"] == "新进"
            else ""
        )
        rendered.append(
            f'<tr data-text="{html.escape(search)}"><td class="number rank">{row["rank"]}</td>'
            f'<td><span class="change {change_class}">{row["rank_change"]}</span></td>'
            f'<td class="code">{row["stock_code"]}</td>'
            f'<td class="name">{html.escape(row["stock_name"])}</td>'
            f'<td class="number">{format_yi(row["total_market_value_wan"])}</td>'
            f'<td class="number count">{row["fund_count"]}</td></tr>'
        )
    return "".join(rendered)


def save_html(quarters, summaries, annual_years, annual_summaries, fund_count):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    start, end = quarter_key(*quarters[0]), quarter_key(*quarters[-1])
    path = os.path.join(OUTPUT_DIR, f"{start}_至_{end}_三星基金持仓汇总.html")
    buttons, sections = [], []
    for index, (year, quarter) in enumerate(quarters):
        key = quarter_key(year, quarter)
        summary = summaries[key]
        buttons.append(
            f'<button class="quarter-button{" active" if index == 0 else ""}" '
            f'data-quarter="{key}">{quarter_label(year, quarter)} <strong>{len(summary["rows"])}</strong></button>'
        )
        sections.append(
            f'<section class="quarter-section{" active" if index == 0 else ""}" data-quarter="{key}">'
            f'<div class="metrics"><div><span>三星基金组合</span><strong>{fund_count}</strong></div>'
            f'<div><span>披露持仓基金</span><strong>{summary["reporting_funds"]}</strong></div>'
            f'<div><span>持仓股票数量</span><strong>{len(summary["rows"])}</strong></div></div>'
            f'<div class="table-wrap"><table><thead><tr><th>排名</th><th>较上季度排名变化</th>'
            f'<th>股票代码</th><th>股票简称</th>'
            f'<th>总市值</th><th>持有该股票的基金数量</th></tr></thead><tbody>{render_rows(summary["rows"])}</tbody></table></div></section>'
        )
    for year in annual_years:
        key = f"{year}-Y"
        summary = annual_summaries[year]
        buttons.append(
            f'<button class="quarter-button annual" data-quarter="{key}">'
            f'{year}年度 <strong>{len(summary["rows"])}</strong></button>'
        )
        sections.append(
            f'<section class="quarter-section" data-quarter="{key}">'
            f'<div class="metrics"><div><span>三星基金组合</span><strong>{fund_count}</strong></div>'
            f'<div><span>四季度披露基金次数</span><strong>{summary["reporting_funds_sum"]}</strong></div>'
            f'<div><span>年度持仓股票数量</span><strong>{len(summary["rows"])}</strong></div></div>'
            f'<div class="table-wrap"><table><thead><tr><th>排名</th><th>较上年度排名变化</th>'
            f'<th>股票代码</th><th>股票简称</th><th>总市值</th>'
            f'<th>持有该股票的基金数量</th></tr></thead><tbody>'
            f'{render_rows(summary["rows"])}</tbody></table></div></section>'
        )
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{start} 至 {end} 三星基金持仓汇总</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#172033;background:#f4f6f8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{background:#17365d;color:white;padding:22px 28px}}h1{{margin:0 0 7px;font-size:24px;letter-spacing:0}}header p{{margin:0;color:#dbe7f5;font-size:14px}}main{{max-width:1250px;margin:auto;padding:20px}}
.toolbar{{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:14px}}input,button{{height:38px;border:1px solid #cbd5e1;border-radius:5px;background:white;padding:0 12px;font-size:14px}}input{{width:min(350px,100%)}}button{{cursor:pointer}}button.active{{background:#17365d;border-color:#17365d;color:white}}
.quarter-section{{display:none}}.quarter-section.active{{display:block}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(130px,220px));gap:10px;margin-bottom:14px}}.metrics div{{background:white;border:1px solid #d8e0e8;padding:13px}}.metrics span{{display:block;color:#64748b;font-size:13px}}.metrics strong{{font-size:22px}}
.table-wrap{{overflow:auto;max-height:calc(100vh - 255px);background:white;border:1px solid #d8e0e8}}table{{width:100%;min-width:700px;border-collapse:collapse}}th{{position:sticky;top:0;background:#1f4e78;color:white;text-align:left;padding:11px 12px;font-size:13px}}td{{padding:10px 12px;border-bottom:1px solid #e6ebf0;font-size:14px}}tbody tr:hover{{background:#f1f6fb}}.code,.number{{white-space:nowrap;font-variant-numeric:tabular-nums}}.name{{font-weight:600}}.count{{color:#c62828;font-weight:700}}
.rank{{font-weight:700}}.change{{display:inline-block;min-width:42px;padding:3px 7px;border-radius:4px;text-align:center;background:#eef2f6;color:#475569;font-size:12px;font-weight:700}}.change.up{{background:#dcfce7;color:#166534}}.change.down{{background:#fee2e2;color:#991b1b}}.change.new{{background:#e0f2fe;color:#075985}}
</style></head><body><header><h1>{start} 至 {end} 三星基金持仓汇总</h1><p>总市值为三星级基金对该股票的持仓市值合计 · 年度数据为四个季度数据之和 · 同一基金 A/C 等份额按一个基金组合计算 · 表格按持有基金数量及总市值倒排</p></header>
<main><div class="toolbar"><input id="search" type="search" placeholder="搜索股票代码或简称">{"".join(buttons)}</div>{"".join(sections)}</main>
<script>const search=document.querySelector("#search");let active=document.querySelector(".quarter-button.active").dataset.quarter;
function filterRows(){{const term=search.value.trim().toLowerCase();document.querySelectorAll(`.quarter-section[data-quarter="${{active}}"] tbody tr`).forEach(row=>row.hidden=!!term&&!row.dataset.text.includes(term));}}
search.addEventListener("input",filterRows);document.querySelectorAll(".quarter-button").forEach(button=>button.addEventListener("click",()=>{{document.querySelectorAll(".quarter-button,.quarter-section").forEach(x=>x.classList.remove("active"));button.classList.add("active");active=button.dataset.quarter;document.querySelector(`.quarter-section[data-quarter="${{active}}"]`).classList.add("active");filterRows();}}));</script></body></html>"""
    with open(path, "w", encoding="utf-8") as file:
        file.write(document)
    return path


def main():
    args = parse_args()
    quarters = quarter_range(parse_quarter(args.start), parse_quarter(args.end))
    comparison_quarter = previous_quarter(*quarters[0])
    current_year = datetime.now().year
    annual_years = sorted({year for year, _ in quarters if year < current_year})
    annual_required_years = set(annual_years)
    if args.annual_compare_prior_year and annual_years:
        annual_required_years.add(annual_years[0] - 1)
    annual_required_years = sorted(annual_required_years)
    annual_required_quarters = [
        (year, quarter)
        for year in annual_required_years
        for quarter in range(1, 5)
    ]
    required_quarters = sorted(
        {comparison_quarter, *quarters, *annual_required_quarters}
    )
    fund_payload = load_three_star_funds(args.refresh_funds)
    funds = fund_payload["funds"]
    summaries = aggregate_quarters(
        funds, required_quarters, refresh=args.refresh_holdings, dry_run=args.dry_run
    )
    if args.dry_run:
        return
    add_rank_changes(summaries, quarters)
    annual_summaries = build_annual_summaries(summaries, annual_required_years)
    add_annual_rank_changes(annual_summaries, annual_years)
    save_csvs(quarters, summaries)
    save_annual_csvs(annual_years, annual_summaries)
    page = save_html(
        quarters, summaries, annual_years, annual_summaries, len(funds)
    )
    enhance_html_sorting.enhance_file(page)
    html_output_cleanup.finalize_html(page)
    print("\n[完成]")
    for quarter in quarters:
        summary = summaries[quarter_key(*quarter)]
        print(
            f"  {quarter_label(*quarter)}："
            f"{summary['reporting_funds']} 个基金披露，{len(summary['rows'])} 支股票"
        )
    for year in annual_years:
        print(f"  {year}年度：{len(annual_summaries[year]['rows'])} 支股票")
    print(f"[整合网页] {os.path.abspath(page)}")
    print(f"[缓存目录] {os.path.abspath(CACHE_DIR)}")


if __name__ == "__main__":
    main()
