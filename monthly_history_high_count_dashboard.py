#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
按月抓取“出现历史新高的次数”，生成任意起止年月的 CSV 和整合 HTML。

数据按历史新高次数倒排，次数相同时按总市值倒排。每月数据独立缓存，
后续运行只抓取尚未缓存的月份。
"""

import argparse
import csv
import html
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

PROJECT_VENV_SITE = (
    Path(__file__).resolve().parent
    / ".venv"
    / "lib"
    / f"python{sys.version_info.major}.{sys.version_info.minor}"
    / "site-packages"
)
if PROJECT_VENV_SITE.exists():
    sys.path.insert(0, str(PROJECT_VENV_SITE))

import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())

import undetected_chromedriver as uc
from selenium.webdriver.common.by import By

import enhance_html_sorting
import html_output_cleanup


CACHE_DIR = "monthly_history_high_count_cache"
OUTPUT_DIR = "monthly_history_high_count_results"
BASE_URL = "https://www.iwencai.com/screener/result?querytype=stock&w="
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CACHE_VERSION = 2


def parse_args():
    now = datetime.now()
    default_month = now.month - 1 if now.month > 1 else 12
    default_year = now.year if now.month > 1 else now.year - 1
    parser = argparse.ArgumentParser(description="生成月度历史新高次数整合网页")
    parser.add_argument("--start", default=f"{default_year}-01", help="起始年月 YYYY-MM")
    parser.add_argument(
        "--end",
        default=f"{default_year}-{default_month:02d}",
        help="结束年月 YYYY-MM，默认最近已结束月份",
    )
    parser.add_argument("--refresh", action="store_true", help="强制重新抓取区间内月份")
    parser.add_argument("--headless", action="store_true", help="无界面浏览器运行")
    parser.add_argument("--dry-run", action="store_true", help="仅显示缓存与抓取计划")
    return parser.parse_args()


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


def month_key(year, month):
    return f"{year}-{month:02d}"


def month_label(year, month):
    return f"{year}年{month}月"


def build_query(year, month):
    label = month_label(year, month)
    return (
        f"{label}出现历史新高的次数，"
        "市值，行业板块，上市时间，"
        "非北交，非新股，非ST，"
        "市值大于200亿，上市时间在2024年之前"
    )


def parse_number(text):
    match = re.search(r"-?[\d,.]+", text or "")
    return float(match.group(0).replace(",", "")) if match else None


def parse_market_cap_yi(text):
    value = parse_number(text)
    if value is None:
        return None
    if "万亿" in (text or ""):
        return value * 10000
    if "万" in (text or "") and "亿" not in (text or ""):
        return value / 10000
    return value


def cache_path(year, month):
    return os.path.join(CACHE_DIR, f"{month_key(year, month)}.json")


def csv_path(year, month):
    return os.path.join(OUTPUT_DIR, f"{month_key(year, month)}_历史新高次数.csv")


def load_cache(year, month):
    path = cache_path(year, month)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    return payload


def save_cache(year, month, records):
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {
        "cache_version": CACHE_VERSION,
        "month": month_key(year, month),
        "query": build_query(year, month),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "records": records,
    }
    with open(cache_path(year, month), "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
    return payload


def get_chrome_major_version():
    try:
        output = subprocess.check_output(
            [CHROME_BINARY, "--version"], stderr=subprocess.STDOUT
        ).decode("utf-8", errors="replace")
    except (OSError, subprocess.CalledProcessError):
        return None
    match = re.search(r"(\d+)\.", output)
    return int(match.group(1)) if match else None


def init_driver(headless=False):
    options = uc.ChromeOptions()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    if headless:
        options.add_argument("--headless=new")
    chrome_major = get_chrome_major_version()
    if chrome_major:
        print(f"[浏览器] Chrome 主版本 {chrome_major}，自动匹配 ChromeDriver")
        return uc.Chrome(options=options, version_main=chrome_major)
    print("[浏览器] 未识别 Chrome 版本，使用自动检测")
    return uc.Chrome(options=options)


def wait_for_data(driver, timeout=45):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if driver.find_elements(By.CSS_SELECTOR, "table tbody tr td"):
            time.sleep(2)
            return True
        time.sleep(1)
    return False


def get_page_records(driver):
    rows = driver.execute_script(
        r"""
        const tables = Array.from(document.querySelectorAll('table'));
        let best = null, maxRows = 0;
        for (const table of tables) {
          const n = table.querySelectorAll('tbody tr').length;
          if (n > maxRows) { maxRows = n; best = table; }
        }
        if (!best) return [];
        return Array.from(best.querySelectorAll('tbody tr')).map(row =>
          Array.from(row.querySelectorAll('td')).map(td => td.innerText.trim())
        );
        """
    )
    records = []
    for cells in rows or []:
        if len(cells) < 9 or not re.fullmatch(r"\d{6}", cells[2].strip()):
            continue
        high_count = parse_number(cells[6])
        market_cap = cells[7].strip()
        records.append(
            {
                "code": cells[2].strip(),
                "name": cells[3].strip(),
                "mktcap": market_cap,
                "market_cap_yi": parse_market_cap_yi(market_cap),
                "industry": cells[8].strip(),
                "history_high_count": int(high_count) if high_count is not None else None,
            }
        )
    return records


def pagination_info(driver):
    return driver.execute_script(
        """
        const ul = document.querySelector('ul.pcwencai-pagination');
        if (!ul) return {maxPage:1,nextDisabled:true};
        const items=Array.from(ul.querySelectorAll('li'));let maxPage=1;
        for(const li of items){const n=parseInt(li.innerText.trim());if(!Number.isNaN(n))maxPage=Math.max(maxPage,n);}
        const legacyNext=items.find(li=>['下页','下一页'].includes(li.innerText.trim()));
        const next=document.querySelector('.pcwencai-pagination-wrap .next-link, .paginate-cus-container .next-link')||legacyNext;
        const nextDisabled=!next||next.className.includes('disabled')||next.hasAttribute('disabled')||next.getAttribute('aria-disabled')==='true';
        return {maxPage,nextDisabled};
        """
    )


def click_next(driver, target_page):
    driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
    time.sleep(0.8)
    result = driver.execute_script(
        f"""
        const ul=document.querySelector('ul.pcwencai-pagination');if(!ul)return 'none';
        const items=Array.from(ul.querySelectorAll('li'));
        for(const li of items){{if(li.innerText.trim()==='{target_page}'){{(li.querySelector('a,span,button')||li).click();return 'clicked';}}}}
        const legacyNext=items.find(li=>['下页','下一页'].includes(li.innerText.trim()));
        const next=document.querySelector('.pcwencai-pagination-wrap .next-link, .paginate-cus-container .next-link')||legacyNext;
        if(next&&!next.className.includes('disabled')&&next.getAttribute('aria-disabled')!=='true'){{next.scrollIntoView({{block:'center'}});(next.querySelector('a,span,button')||next).click();return 'clicked next';}}
        return 'not found';
        """
    )
    print(f"  [翻页] {result}")
    return result and "clicked" in result


def wait_for_page_change(driver, old_code, timeout=15):
    for _ in range(timeout):
        time.sleep(1)
        records = get_page_records(driver)
        if records and records[0]["code"] != old_code:
            return True
    return False


def scrape_month(driver, year, month):
    label = month_label(year, month)
    query = build_query(year, month)
    print(f"\n[问财条件] {query}")
    print(f"[开始抓取] {label}")
    driver.get(BASE_URL + quote(query))
    time.sleep(6)
    if not wait_for_data(driver):
        raise RuntimeError(f"{label} 问财表格未加载")

    all_records, seen, page = [], set(), 1
    while True:
        records = get_page_records(driver)
        new_records = [record for record in records if record["code"] not in seen]
        for record in new_records:
            seen.add(record["code"])
            all_records.append(record)
        print(f"  第 {page} 页：本页 {len(records)} 条，新增 {len(new_records)} 条，累计 {len(all_records)} 条")
        info = pagination_info(driver)
        if info["nextDisabled"] or page >= info["maxPage"]:
            break
        old_code = records[0]["code"] if records else ""
        if not click_next(driver, page + 1) or not wait_for_page_change(driver, old_code):
            break
        page += 1

    valid = [
        record for record in all_records
        if record["market_cap_yi"] is not None
        and record["market_cap_yi"] > 200
        and record["history_high_count"] is not None
        and record["history_high_count"] > 0
    ]
    valid.sort(
        key=lambda record: (record["history_high_count"], record["market_cap_yi"]),
        reverse=True,
    )
    print(f"[{label}] 保留 {len(valid)} 条")
    return valid


def load_or_fetch(args, periods):
    payloads, missing = {}, []
    for period in periods:
        cached = None if args.refresh else load_cache(*period)
        if cached:
            payloads[period] = cached
            print(f"[使用缓存] {month_label(*period)}：{len(cached['records'])} 条")
        else:
            missing.append(period)
    if args.dry_run:
        print(f"[缓存目录] {os.path.abspath(CACHE_DIR)}")
        print(f"[已有缓存] {', '.join(month_label(*p) for p in payloads) or '无'}")
        print(f"[需要抓取] {', '.join(month_label(*p) for p in missing) or '无'}")
        return payloads
    if missing:
        driver = init_driver(args.headless)
        try:
            for period in missing:
                payloads[period] = save_cache(*period, scrape_month(driver, *period))
        finally:
            driver.quit()
    return payloads


def build_previous_maps(payloads, periods):
    result, code_months = {}, {}
    for period in periods:
        result[period] = {code: labels.copy() for code, labels in code_months.items()}
        label = month_label(*period)
        for record in payloads[period]["records"]:
            code_months.setdefault(record["code"], []).append(label)
    return result


def save_csv(year, month, records, previous_map):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    fields = [
        "股票代码",
        "股票简称",
        "同花顺行业",
        "总市值",
        "历史新高次数",
        "区间内此前出现月份数量",
        "区间内此前出现月份",
    ]
    with open(csv_path(year, month), "w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        for record in records:
            previous = previous_map.get(record["code"], [])
            writer.writerow(
                {
                    "股票代码": record["code"],
                    "股票简称": record["name"],
                    "同花顺行业": record["industry"],
                    "总市值": record["mktcap"],
                    "历史新高次数": record["history_high_count"],
                    "区间内此前出现月份数量": len(previous),
                    "区间内此前出现月份": "、".join(previous) if previous else "未出现",
                }
            )


def save_dashboard(periods, payloads, previous_maps):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    start_key, end_key = month_key(*periods[0]), month_key(*periods[-1])
    output = os.path.join(OUTPUT_DIR, f"{start_key}_至_{end_key}_历史新高次数整合看板.html")
    buttons, sections = [], []
    for index, period in enumerate(periods):
        period_id, records = month_key(*period), payloads[period]["records"]
        previous_map = previous_maps[period]
        buttons.append(f'<button class="month-button{" active" if index == 0 else ""}" data-month="{period_id}">{month_label(*period)} <strong>{len(records)}</strong></button>')
        rows = []
        for record in records:
            previous = previous_map.get(record["code"], [])
            previous_text = "、".join(previous) if previous else "未出现"
            search = (record["code"] + record["name"] + record["industry"]).lower()
            rows.append(
                f'<tr data-text="{html.escape(search)}" data-count="{record["history_high_count"]}">'
                f'<td class="code">{html.escape(record["code"])}</td><td class="name">{html.escape(record["name"])}</td>'
                f'<td>{html.escape(record["industry"])}</td><td class="number">{html.escape(record["mktcap"])}</td>'
                f'<td class="number count">{record["history_high_count"]}</td>'
                f'<td class="number">{len(previous)}</td><td>{html.escape(previous_text)}</td></tr>'
            )
        max_count = records[0]["history_high_count"] if records else 0
        sections.append(
            f'<section class="month-section{" active" if index == 0 else ""}" data-month="{period_id}">'
            f'<div class="metrics"><div><span>股票数量</span><strong>{len(records)}</strong></div>'
            f'<div><span>当月最高次数</span><strong>{max_count}</strong></div>'
            f'<div><span>区间首次出现</span><strong>{sum(not previous_map.get(r["code"]) for r in records)}</strong></div></div>'
            f'<div class="table-wrap"><table><thead><tr><th>股票代码</th><th>股票简称</th><th>同花顺行业</th><th>总市值</th><th>历史新高次数</th><th>此前月份数量</th><th>区间内此前出现月份</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>'
        )
    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{start_key} 至 {end_key} 历史新高次数</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#172033;background:#f4f6f8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{background:#17365d;color:white;padding:22px 28px}}h1{{margin:0 0 7px;font-size:24px;letter-spacing:0}}header p{{margin:0;color:#dbe7f5;font-size:14px}}main{{max-width:1500px;margin:auto;padding:20px}}
.toolbar{{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:14px}}input,button{{height:38px;border:1px solid #cbd5e1;border-radius:5px;background:white;padding:0 12px;font-size:14px}}input{{width:min(360px,100%)}}button{{cursor:pointer}}button.active{{background:#17365d;border-color:#17365d;color:white}}
.month-section{{display:none}}.month-section.active{{display:block}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(120px,220px));gap:10px;margin-bottom:14px}}.metrics div{{background:white;border:1px solid #d8e0e8;padding:13px}}.metrics span{{display:block;color:#64748b;font-size:13px}}.metrics strong{{font-size:22px}}
.table-wrap{{overflow:auto;max-height:calc(100vh - 255px);background:white;border:1px solid #d8e0e8}}table{{width:100%;min-width:950px;border-collapse:collapse}}th{{position:sticky;top:0;background:#1f4e78;color:white;text-align:left;padding:11px 12px;font-size:13px}}td{{padding:10px 12px;border-bottom:1px solid #e6ebf0;font-size:14px}}tbody tr:hover{{background:#f1f6fb}}.code,.number{{white-space:nowrap;font-variant-numeric:tabular-nums}}.name{{font-weight:600}}.count{{color:#c62828;font-weight:700}}
</style></head><body><header><h1>{start_key} 至 {end_key} 历史新高次数</h1><p>市值大于200亿元 · 非北交/非新股/非ST · 2024年之前上市 · 按历史新高次数及总市值倒排 · 历史月份使用独立缓存</p></header>
<main><div class="toolbar"><input id="search" type="search" placeholder="搜索股票代码、简称或行业">{"".join(buttons)}</div>{"".join(sections)}</main>
<script>const search=document.querySelector("#search");let activeMonth=document.querySelector(".month-button.active").dataset.month;
function filterRows(){{const term=search.value.trim().toLowerCase();document.querySelectorAll(`.month-section[data-month="${{activeMonth}}"] tbody tr`).forEach(row=>row.hidden=!!term&&!row.dataset.text.includes(term));}}
search.addEventListener("input",filterRows);document.querySelectorAll(".month-button").forEach(button=>button.addEventListener("click",()=>{{document.querySelectorAll(".month-button,.month-section").forEach(x=>x.classList.remove("active"));button.classList.add("active");activeMonth=button.dataset.month;document.querySelector(`.month-section[data-month="${{activeMonth}}"]`).classList.add("active");filterRows();}}));</script></body></html>"""
    with open(output, "w", encoding="utf-8") as file:
        file.write(document)
    return output


def main():
    args = parse_args()
    periods = period_range(parse_year_month(args.start), parse_year_month(args.end))
    payloads = load_or_fetch(args, periods)
    if args.dry_run:
        return
    previous_maps = build_previous_maps(payloads, periods)
    for period in periods:
        save_csv(*period, payloads[period]["records"], previous_maps[period])
    dashboard = save_dashboard(periods, payloads, previous_maps)
    enhance_html_sorting.enhance_file(dashboard)
    html_output_cleanup.finalize_html(dashboard)
    print("\n[完成]")
    for period in periods:
        print(f"  {month_label(*period)}：{len(payloads[period]['records'])} 条")
    print(f"[整合网页] {os.path.abspath(dashboard)}")
    print(f"[缓存目录] {os.path.abspath(CACHE_DIR)}")


if __name__ == "__main__":
    main()
