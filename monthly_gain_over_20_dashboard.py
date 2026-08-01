#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
按月抓取涨幅大于 20% 的股票并生成整合 HTML。

支持从任意起始年月生成到任意结束年月。每个月的数据首次抓取后保存到本地
缓存，后续运行只抓缺失月份，不重复抓取历史月份。
"""

import argparse
import csv
import html
import json
import os
from collections import Counter
from datetime import datetime

import may_2026_gain_over_20 as scraper
import enhance_html_sorting
import html_output_cleanup


CACHE_DIR = "monthly_gain_over_20_cache"
OUTPUT_DIR = "monthly_gain_over_20_results"


def parse_args():
    now = datetime.now()
    default_through_month = now.month - 1 if now.month > 1 else 12
    default_year = now.year if now.month > 1 else now.year - 1
    default_end = f"{default_year}-{default_through_month:02d}"

    parser = argparse.ArgumentParser(description="生成月度涨幅大于20%股票整合网页")
    parser.add_argument(
        "--start",
        default=f"{default_year}-01",
        help="起始年月，格式 YYYY-MM，默认当年1月",
    )
    parser.add_argument(
        "--end",
        default=default_end,
        help="结束年月，格式 YYYY-MM，默认最近已结束月份",
    )
    parser.add_argument("--year", type=int, help="兼容旧参数：目标年份")
    parser.add_argument(
        "--through-month",
        type=int,
        help="兼容旧参数：生成到目标年份的哪个月份",
    )
    parser.add_argument(
        "--month",
        type=int,
        action="append",
        help="兼容旧参数：只处理目标年份的指定月份，可重复使用",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="强制重新抓取指定月份；默认优先使用缓存",
    )
    parser.add_argument("--headless", action="store_true", help="无界面浏览器运行")
    parser.add_argument("--dry-run", action="store_true", help="仅显示本次抓取计划")
    return parser.parse_args()


def month_key(year, month):
    return f"{year}-{month:02d}"


def month_label(year, month):
    return f"{year}年{month}月"


def parse_year_month(value):
    try:
        parsed = datetime.strptime(value, "%Y-%m")
    except ValueError as exc:
        raise ValueError(f"年月格式必须为 YYYY-MM：{value}") from exc
    return parsed.year, parsed.month


def period_range(start, end):
    if start > end:
        raise ValueError(f"起始年月不能晚于结束年月：{start} > {end}")
    periods = []
    year, month = start
    while (year, month) <= end:
        periods.append((year, month))
        month += 1
        if month == 13:
            year += 1
            month = 1
    return periods


def cache_path(year, month):
    return os.path.join(CACHE_DIR, f"{month_key(year, month)}.json")


def monthly_csv_path(year, month):
    return os.path.join(OUTPUT_DIR, f"{month_key(year, month)}_涨幅大于20.csv")


def load_cache(year, month):
    path = cache_path(year, month)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as file:
        return json.load(file)


def save_cache(year, month, records):
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {
        "month": month_key(year, month),
        "query": scraper.build_query(month_label(year, month)),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "records": records,
    }
    with open(cache_path(year, month), "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return payload


def save_monthly_csv(year, month, records, previous_code_months):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fields = [
        "股票代码",
        "股票简称",
        "同花顺行业",
        "总市值",
        f"{year}年{month}月涨跌幅",
        "此前涨幅大于20%月份数量",
        "此前涨幅大于20%月份",
    ]
    with open(monthly_csv_path(year, month), "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            previous = previous_code_months.get(record["code"], [])
            writer.writerow(
                {
                    "股票代码": record["code"],
                    "股票简称": record["name"],
                    "同花顺行业": record["industry"],
                    "总市值": record["mktcap"],
                    f"{year}年{month}月涨跌幅": f'{record["gain_pct"]:.2f}%',
                    "此前涨幅大于20%月份数量": len(previous),
                    "此前涨幅大于20%月份": "、".join(previous) if previous else "未出现",
                }
            )


def fetch_month(driver, year, month):
    label = month_label(year, month)
    query = scraper.build_query(label)
    url = scraper.build_url(query)
    raw_records = scraper.scrape_all_pages(driver, url, query, label)
    valid, rejected = scraper.validate_records(raw_records)
    print(f"[{label}] 抓取 {len(raw_records)} 条，本地剔除 {len(rejected)} 条，保留 {len(valid)} 条")
    if rejected:
        reason_counts = Counter(
            reason for item in rejected for reason in item.get("reasons", [])
        )
        reason_text = "，".join(
            f"{reason} {count} 条" for reason, count in reason_counts.most_common()
        )
        print(f"  [剔除原因] {reason_text}")
    return valid


def prepare_periods(args):
    if args.month:
        year = args.year or parse_year_month(args.start)[0]
        months = sorted(set(args.month))
        invalid = [month for month in months if month < 1 or month > 12]
        if invalid:
            raise ValueError(f"月份必须在 1-12 之间：{invalid}")
        return [(year, month) for month in months]

    if args.year or args.through_month:
        year = args.year or parse_year_month(args.start)[0]
        through_month = args.through_month or 12
        if through_month < 1 or through_month > 12:
            raise ValueError("through-month 必须在 1-12 之间")
        return [(year, month) for month in range(1, through_month + 1)]

    return period_range(parse_year_month(args.start), parse_year_month(args.end))


def load_or_fetch_periods(args, periods):
    payloads = {}
    missing = []
    for year, month in periods:
        cached = None if args.refresh else load_cache(year, month)
        if cached:
            payloads[(year, month)] = cached
            print(f"[使用缓存] {month_label(year, month)}：{len(cached['records'])} 条")
        else:
            missing.append((year, month))

    if args.dry_run:
        print(f"[缓存目录] {os.path.abspath(CACHE_DIR)}")
        print(f"[已有缓存] {', '.join(month_label(y, m) for y, m in payloads) or '无'}")
        print(f"[需要抓取] {', '.join(month_label(y, m) for y, m in missing) or '无'}")
        return payloads

    if missing:
        driver = scraper.init_driver(headless=args.headless)
        try:
            for year, month in missing:
                records = fetch_month(driver, year, month)
                payloads[(year, month)] = save_cache(year, month, records)
        finally:
            driver.quit()
    return payloads


def build_previous_month_maps(payloads, periods):
    previous_by_month = {}
    code_months = {}
    for period in periods:
        previous_by_month[period] = {
            code: labels.copy() for code, labels in code_months.items()
        }
        label = month_label(*period)
        for record in payloads[period]["records"]:
            code_months.setdefault(record["code"], []).append(label)
    return previous_by_month


def save_dashboard(periods, payloads, previous_by_month):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    start_key = month_key(*periods[0])
    end_key = month_key(*periods[-1])
    output_path = os.path.join(
        OUTPUT_DIR, f"{start_key}_至_{end_key}_月度涨幅大于20_整合看板.html"
    )
    month_buttons = []
    sections = []

    for index, period in enumerate(periods):
        year, month = period
        period_id = month_key(year, month)
        records = payloads[period]["records"]
        previous_map = previous_by_month[period]
        new_count = sum(not previous_map.get(record["code"]) for record in records)
        repeat_count = len(records) - new_count
        month_buttons.append(
            f'<button class="month-button{" active" if index == 0 else ""}" '
            f'data-month="{period_id}">{year}年{month}月 <strong>{len(records)}</strong></button>'
        )

        rows = []
        for record in records:
            previous = previous_map.get(record["code"], [])
            previous_text = "、".join(previous) if previous else "未出现"
            mark_class = "new" if not previous else "repeat"
            rows.append(
                f"""<tr data-text="{html.escape((record["code"] + record["name"] + record["industry"]).lower())}">
                <td class="code">{html.escape(record["code"])}</td>
                <td class="name">{html.escape(record["name"])}</td>
                <td>{html.escape(record["industry"])}</td>
                <td class="number">{html.escape(record["mktcap"])}</td>
                <td class="number gain">{record["gain_pct"]:.2f}%</td>
                <td class="number">{len(previous)}</td>
                <td><span class="mark {mark_class}">{html.escape(previous_text)}</span></td>
                </tr>"""
            )

        sections.append(
            f"""<section class="month-section{" active" if index == 0 else ""}" data-month="{period_id}">
              <div class="metrics">
                <div><span>当月股票</span><strong>{len(records)}</strong></div>
                <div><span>此前未出现</span><strong>{new_count}</strong></div>
                <div><span>此前已出现</span><strong>{repeat_count}</strong></div>
              </div>
              <div class="table-wrap"><table>
                <thead><tr><th>股票代码</th><th>股票简称</th><th>同花顺行业</th><th>总市值</th><th>{year}年{month}月涨跌幅</th><th>此前月份数量</th><th>区间内此前涨幅大于20%月份</th></tr></thead>
                <tbody>{"".join(rows)}</tbody>
              </table></div>
            </section>"""
        )

    document = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{start_key} 至 {end_key} 月度涨幅大于20%股票</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;color:#172033;background:#f4f6f8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{background:#17365d;color:#fff;padding:22px 28px}}h1{{margin:0 0 7px;font-size:24px;letter-spacing:0}}header p{{margin:0;color:#dbe7f5;font-size:14px}}
main{{max-width:1500px;margin:auto;padding:20px}}.toolbar{{display:flex;flex-wrap:wrap;gap:9px;align-items:center;margin-bottom:14px}}
input,button{{height:38px;border:1px solid #cbd5e1;border-radius:5px;background:#fff;padding:0 12px;font-size:14px}}input{{width:min(360px,100%)}}button{{cursor:pointer;color:#334155}}button.active{{background:#17365d;border-color:#17365d;color:#fff}}
.month-section{{display:none}}.month-section.active{{display:block}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(120px,220px));gap:10px;margin-bottom:14px}}
.metrics div{{background:#fff;border:1px solid #d8e0e8;padding:13px}}.metrics span{{display:block;color:#64748b;font-size:13px}}.metrics strong{{display:block;margin-top:4px;font-size:22px}}
.table-wrap{{overflow:auto;max-height:calc(100vh - 255px);background:#fff;border:1px solid #d8e0e8}}table{{width:100%;min-width:980px;border-collapse:collapse}}
th{{position:sticky;top:0;z-index:1;text-align:left;padding:11px 12px;color:#fff;background:#1f4e78;font-size:13px}}td{{padding:10px 12px;border-bottom:1px solid #e6ebf0;font-size:14px}}
tbody tr:hover{{background:#f1f6fb}}.code,.number{{white-space:nowrap;font-variant-numeric:tabular-nums}}.name{{font-weight:600;white-space:nowrap}}.gain{{color:#c62828;font-weight:700}}
.mark{{display:inline-block;padding:3px 8px;border-radius:4px;white-space:nowrap;font-size:12px;font-weight:600}}.mark.new{{color:#166534;background:#dcfce7}}.mark.repeat{{color:#854d0e;background:#fef9c3}}
@media(max-width:700px){{header{{padding:18px}}main{{padding:12px}}.metrics{{grid-template-columns:repeat(3,1fr)}}.table-wrap{{max-height:calc(100vh - 310px)}}}}
</style></head><body>
<header><h1>{start_key} 至 {end_key} 月度涨幅大于20%股票</h1><p>市值大于200亿元 · 非北交/非新股/非ST · 2024年之前上市 · 各月按总市值倒排 · 标识所选区间内此前出现月份 · 历史月份使用本地缓存</p></header>
<main><div class="toolbar"><input id="search" type="search" placeholder="搜索当前月份的股票代码、简称或行业">{"".join(month_buttons)}</div>{"".join(sections)}</main>
<script>
const search=document.querySelector("#search");let activeMonth=document.querySelector(".month-button.active").dataset.month;
function filterRows(){{const term=search.value.trim().toLowerCase();document.querySelectorAll(`.month-section[data-month="${{activeMonth}}"] tbody tr`).forEach(row=>row.hidden=!!term&&!row.dataset.text.includes(term));}}
search.addEventListener("input",filterRows);document.querySelectorAll(".month-button").forEach(button=>button.addEventListener("click",()=>{{document.querySelectorAll(".month-button,.month-section").forEach(x=>x.classList.remove("active"));button.classList.add("active");activeMonth=button.dataset.month;document.querySelector(`.month-section[data-month="${{activeMonth}}"]`).classList.add("active");filterRows();}}));
</script></body></html>"""
    with open(output_path, "w", encoding="utf-8") as file:
        file.write(document)
    return output_path


def main():
    args = parse_args()
    periods = prepare_periods(args)
    payloads = load_or_fetch_periods(args, periods)
    if args.dry_run:
        return

    previous_by_month = build_previous_month_maps(payloads, periods)
    for year, month in periods:
        save_monthly_csv(
            year,
            month,
            payloads[(year, month)]["records"],
            previous_by_month[(year, month)],
        )
    dashboard = save_dashboard(periods, payloads, previous_by_month)
    enhance_html_sorting.enhance_file(dashboard)
    html_output_cleanup.finalize_html(dashboard)

    print("\n[完成]")
    for year, month in periods:
        print(f"  {month_label(year, month)}：{len(payloads[(year, month)]['records'])} 条")
    print(f"[整合网页] {os.path.abspath(dashboard)}")
    print(f"[缓存目录] {os.path.abspath(CACHE_DIR)}")


if __name__ == "__main__":
    main()
