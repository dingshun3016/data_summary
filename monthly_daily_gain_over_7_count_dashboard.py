#!/usr/bin/python
# -*- coding: utf-8 -*-
"""
按月抓取“当月单日涨幅大于指定阈值的次数”，生成 CSV 和整合 HTML。

默认筛选：单日涨幅大于 7%，总市值大于 200 亿元，非北交/非新股/非ST，
上市时间在 2024 年之前。每个月数据独立缓存，后续运行只抓缺失月份。
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


CACHE_DIR = "monthly_daily_gain_over_7_count_cache"
OUTPUT_DIR = "monthly_daily_gain_over_7_count_results"
BASE_URL = "https://www.iwencai.com/screener/result?querytype=stock&w="
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CACHE_VERSION = 4


def parse_args():
    now = datetime.now()
    default_month = now.month - 1 if now.month > 1 else 12
    default_year = now.year if now.month > 1 else now.year - 1
    parser = argparse.ArgumentParser(description="生成月度单日涨幅大于7%次数整合网页")
    parser.add_argument("--start", default=f"{default_year}-01", help="起始年月 YYYY-MM")
    parser.add_argument(
        "--end",
        default=f"{default_year}-{default_month:02d}",
        help="结束年月 YYYY-MM，默认最近已结束月份",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=7,
        help="单日涨幅阈值，默认 7，表示涨幅大于7%",
    )
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=200,
        help="最低总市值，单位亿元，默认 200",
    )
    parser.add_argument("--refresh", action="store_true", help="强制重新抓取区间内月份")
    parser.add_argument("--headless", action="store_true", help="无界面浏览器运行")
    parser.add_argument("--dry-run", action="store_true", help="仅显示缓存与抓取计划")
    return parser.parse_args()


def compact_number(value):
    return str(int(value)) if float(value).is_integer() else str(value).rstrip("0").rstrip(".")


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


def condition_key(threshold, min_market_cap):
    return f"gt{compact_number(threshold)}pct_mkt{compact_number(min_market_cap)}yi"


def build_query(year, month, threshold=7, min_market_cap=200):
    label = month_label(year, month)
    threshold_text = compact_number(threshold)
    market_text = compact_number(min_market_cap)
    return (
        f"{year}年{month}月份每天涨幅大于{threshold_text}%的股票数，"
        "行业板块，市值，"
        "非北交，非新股，非st，"
        f"市值大于{market_text}亿，上市时间在2024年之前"
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


def cache_path(year, month, threshold, min_market_cap):
    name = f"{month_key(year, month)}_{condition_key(threshold, min_market_cap)}.json"
    return os.path.join(CACHE_DIR, name)


def csv_path(year, month, threshold, min_market_cap):
    name = (
        f"{month_key(year, month)}_单日涨幅大于"
        f"{compact_number(threshold)}%次数_市值大于{compact_number(min_market_cap)}亿.csv"
    )
    return os.path.join(OUTPUT_DIR, name)


def load_cache(year, month, threshold, min_market_cap):
    path = cache_path(year, month, threshold, min_market_cap)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as file:
        payload = json.load(file)
    if payload.get("cache_version") != CACHE_VERSION:
        return None
    return payload


def save_cache(year, month, records, threshold, min_market_cap):
    os.makedirs(CACHE_DIR, exist_ok=True)
    payload = {
        "cache_version": CACHE_VERSION,
        "month": month_key(year, month),
        "threshold": threshold,
        "min_market_cap": min_market_cap,
        "query": build_query(year, month, threshold, min_market_cap),
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "records": records,
    }
    with open(cache_path(year, month, threshold, min_market_cap), "w", encoding="utf-8") as file:
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


def print_page_diagnostics(driver):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    screenshot = os.path.abspath(f"iwencai_daily_gain_failed_{timestamp}.png")
    html_file = os.path.abspath(f"iwencai_daily_gain_failed_{timestamp}.html")
    try:
        driver.save_screenshot(screenshot)
        with open(html_file, "w", encoding="utf-8") as file:
            file.write(driver.page_source)
        body_text = driver.find_element(By.TAG_NAME, "body").text[:1000]
        print(f"[诊断] 页面标题：{driver.title}")
        print(f"[诊断] 当前 URL：{driver.current_url}")
        print(f"[诊断] 页面文字前1000字：\n{body_text}")
        print(f"[诊断] 截图：{screenshot}")
        print(f"[诊断] HTML：{html_file}")
    except Exception as exc:
        print(f"[诊断] 获取页面信息失败：{exc}")


def get_page_table(driver):
    return driver.execute_script(
        r"""
        const tables = Array.from(document.querySelectorAll('table'));
        let best = null, maxRows = 0;
        for (const table of tables) {
          const n = table.querySelectorAll('tbody tr').length;
          if (n > maxRows) { maxRows = n; best = table; }
        }
        if (!best) return {headers: [], rows: []};
        let headerRow = Array.from(best.querySelectorAll('thead tr'))
          .sort((a, b) => b.querySelectorAll('th').length - a.querySelectorAll('th').length)[0];
        if (!headerRow) {
          const columnCount = best.querySelectorAll('tbody tr:first-child td').length;
          headerRow = Array.from(document.querySelectorAll('thead tr'))
            .filter(row => row.querySelectorAll('th').length === columnCount)
            .sort((a, b) => b.innerText.length - a.innerText.length)[0];
        }
        const headers = headerRow
          ? Array.from(headerRow.querySelectorAll('th')).map(th => th.innerText.trim())
          : [];
        const rows = Array.from(best.querySelectorAll('tbody tr')).map(row =>
          Array.from(row.querySelectorAll('td')).map(td => td.innerText.trim())
        );
        return {headers, rows};
        """
    )


def find_column(headers, keyword_groups):
    for index, header in enumerate(headers):
        compact = re.sub(r"\s+", "", header)
        for keywords in keyword_groups:
            if all(keyword in compact for keyword in keywords):
                return index
    return None


def infer_columns_from_row(cells):
    code_index = next(
        (index for index, cell in enumerate(cells) if re.fullmatch(r"\d{6}", cell.strip())),
        None,
    )
    industry_index = next(
        (
            index
            for index, cell in enumerate(cells)
            if cell.count("-") >= 2
            and re.search(r"[\u4e00-\u9fff]", cell)
            and index >= 2
            and (
                (index > 0 and "亿" in cells[index - 1])
                or (index + 1 < len(cells) and "亿" in cells[index + 1])
            )
        ),
        None,
    )
    if code_index is None or industry_index is None:
        return None
    if industry_index + 1 < len(cells) and "亿" in cells[industry_index + 1]:
        market_cap_index = industry_index + 1
    else:
        market_cap_index = industry_index - 1
    indexes = {
        "code": code_index,
        "name": code_index + 1,
        "count": None,
        "market_cap": market_cap_index,
        "industry": industry_index,
    }
    return indexes


def count_daily_gain_hits(cells, code_index, industry_index, threshold):
    # 问财“每天涨幅大于X%”会把每个交易日的涨跌幅展开成多列：
    # 代码、简称、现价、当前涨跌幅之后，到行业列之前基本都是日期涨跌幅。
    start = min(code_index + 4, industry_index)
    hits = 0
    for cell in cells[start:industry_index]:
        value = parse_number(cell)
        if value is not None and value > threshold:
            hits += 1
    return hits


def get_page_records(driver, threshold=7):
    table = get_page_table(driver) or {}
    headers = table.get("headers") or []
    rows = table.get("rows") or []
    indexes = {
        "code": find_column(headers, [["股票代码"]]),
        "name": find_column(headers, [["股票简称"]]),
        "count": find_column(
            headers,
            [["单日涨幅", "次数"], ["涨幅", "大于", "次数"], ["涨跌幅", "大于", "次数"]],
        ),
        "market_cap": find_column(headers, [["总市值"]]),
        "industry": find_column(headers, [["同花顺行业"], ["所属同花顺行业"]]),
    }
    required_keys = ["code", "name", "market_cap", "industry"]
    if any(indexes[key] is None for key in required_keys):
        indexes = infer_columns_from_row(rows[0]) if rows else None
    if not indexes:
        return []
    required_index = max(index for index in indexes.values() if index is not None)

    records = []
    for cells in rows:
        if len(cells) <= required_index:
            continue
        code = cells[indexes["code"]].strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        if indexes["count"] is not None:
            count = parse_number(cells[indexes["count"]])
        else:
            count = count_daily_gain_hits(
                cells, indexes["code"], indexes["industry"], threshold
            )
        market_cap = cells[indexes["market_cap"]].strip()
        records.append(
            {
                "code": code,
                "name": cells[indexes["name"]].strip(),
                "mktcap": market_cap,
                "market_cap_yi": parse_market_cap_yi(market_cap),
                "industry": cells[indexes["industry"]].strip(),
                "daily_gain_count": int(count) if count is not None else None,
            }
        )
    return records


def pagination_info(driver):
    return driver.execute_script(
        """
        const ul = document.querySelector('ul.pcwencai-pagination');
        if (!ul) return {maxPage: 1, nextDisabled: true};
        const items = Array.from(ul.querySelectorAll('li'));
        let maxPage = 1;
        for (const li of items) {
          const n = parseInt(li.innerText.trim());
          if (!Number.isNaN(n)) maxPage = Math.max(maxPage, n);
        }
        const legacyNext = items.find(li => ['下页', '下一页'].includes(li.innerText.trim()));
        const next = document.querySelector(
          '.pcwencai-pagination-wrap .next-link, .paginate-cus-container .next-link'
        ) || legacyNext;
        const nextDisabled = !next || next.className.includes('disabled') ||
          next.hasAttribute('disabled') || next.getAttribute('aria-disabled') === 'true';
        return {maxPage, nextDisabled};
        """
    )


def click_next(driver, target_page):
    driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
    time.sleep(0.8)
    result = driver.execute_script(
        f"""
        const ul = document.querySelector('ul.pcwencai-pagination');
        if (!ul) return 'none';
        const items = Array.from(ul.querySelectorAll('li'));
        for (const li of items) {{
          if (li.innerText.trim() === '{target_page}') {{
            (li.querySelector('a,span,button') || li).click();
            return 'clicked page';
          }}
        }}
        const legacyNext = items.find(li =>
          ['下页', '下一页'].includes(li.innerText.trim())
        );
        const next = document.querySelector(
          '.pcwencai-pagination-wrap .next-link, .paginate-cus-container .next-link'
        ) || legacyNext;
        if (next && !next.className.includes('disabled') &&
            next.getAttribute('aria-disabled') !== 'true') {{
            next.scrollIntoView({{block: 'center'}});
            (next.querySelector('a,span,button') || next).click();
            return 'clicked next';
        }}
        return 'not found';
        """
    )
    print(f"  [翻页] {result}")
    return result and "clicked" in result


def wait_for_page_change(driver, old_code, threshold, timeout=15):
    for _ in range(timeout):
        time.sleep(1)
        records = get_page_records(driver, threshold)
        if records and records[0]["code"] != old_code:
            return True
    return False


def scrape_month(driver, year, month, threshold, min_market_cap):
    label = month_label(year, month)
    query = build_query(year, month, threshold, min_market_cap)
    print(f"\n[问财条件] {query}")
    print(f"[开始抓取] {label}")
    driver.get(BASE_URL + quote(query))
    time.sleep(6)
    if not wait_for_data(driver):
        print_page_diagnostics(driver)
        raise RuntimeError(f"{label} 问财表格未加载")

    table = get_page_table(driver)
    if table.get("headers"):
        print(f"  [列识别] 已按表头识别 {len(table['headers'])} 列")
    else:
        print("  [列识别] 表头与数据分离，已按次数/总市值/行业结构识别")
    if not get_page_records(driver, threshold):
        print_page_diagnostics(driver)
        raise RuntimeError(f"{label} 未能识别次数、总市值或行业列")

    all_records, seen, page = [], set(), 1
    while True:
        records = get_page_records(driver, threshold)
        new_records = [record for record in records if record["code"] not in seen]
        for record in new_records:
            seen.add(record["code"])
            all_records.append(record)
        print(
            f"  第 {page} 页：本页 {len(records)} 条，"
            f"新增 {len(new_records)} 条，累计 {len(all_records)} 条"
        )
        info = pagination_info(driver)
        if info["nextDisabled"] or page >= info["maxPage"]:
            break
        old_code = records[0]["code"] if records else ""
        if not click_next(driver, page + 1) or not wait_for_page_change(
            driver, old_code, threshold
        ):
            break
        page += 1

    valid, rejected = [], []
    for record in all_records:
        reasons = []
        if record["market_cap_yi"] is None:
            reasons.append("总市值无法解析")
        elif record["market_cap_yi"] <= min_market_cap:
            reasons.append(f"总市值不大于{compact_number(min_market_cap)}亿")
        if record["daily_gain_count"] is None:
            reasons.append("次数无法解析")
        elif record["daily_gain_count"] < 1:
            reasons.append(f"未计算出单日涨幅大于{compact_number(threshold)}%的交易日")
        if reasons:
            rejected.append((record, "；".join(reasons)))
        else:
            valid.append(record)
    valid.sort(key=lambda record: record["market_cap_yi"], reverse=True)
    if rejected:
        print(f"[本地剔除] {len(rejected)} 条")
        for record, reason in rejected[:8]:
            print(
                "  "
                f"{record.get('code', '')} {record.get('name', '')} "
                f"市值={record.get('mktcap', '')} "
                f"次数={record.get('daily_gain_count', '')}：{reason}"
            )
    print(f"[{label}] 保留 {len(valid)} 条")
    return valid


def load_or_fetch(args, periods):
    payloads, missing = {}, []
    for period in periods:
        cached = None if args.refresh else load_cache(
            *period, args.threshold, args.min_market_cap
        )
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
                records = scrape_month(driver, *period, args.threshold, args.min_market_cap)
                payloads[period] = save_cache(
                    *period, records, args.threshold, args.min_market_cap
                )
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


def save_csv(year, month, records, previous_map, threshold, min_market_cap):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    threshold_text = compact_number(threshold)
    fields = [
        "股票代码",
        "股票简称",
        "同花顺行业",
        "总市值",
        f"{year}年{month}月单日涨幅大于{threshold_text}%次数",
        f"区间内此前单日涨幅大于{threshold_text}%月份数量",
        f"区间内此前单日涨幅大于{threshold_text}%月份",
    ]
    with open(
        csv_path(year, month, threshold, min_market_cap),
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:
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
                    f"{year}年{month}月单日涨幅大于{threshold_text}%次数": record[
                        "daily_gain_count"
                    ],
                    f"区间内此前单日涨幅大于{threshold_text}%月份数量": len(previous),
                    f"区间内此前单日涨幅大于{threshold_text}%月份": "、".join(previous)
                    if previous
                    else "未出现",
                }
            )


def save_dashboard(periods, payloads, previous_maps, threshold, min_market_cap):
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    start_key, end_key = month_key(*periods[0]), month_key(*periods[-1])
    threshold_text = compact_number(threshold)
    market_text = compact_number(min_market_cap)
    output = os.path.join(
        OUTPUT_DIR,
        f"{start_key}_至_{end_key}_月度单日涨幅大于{threshold_text}%次数_市值大于{market_text}亿_整合看板.html",
    )
    buttons, sections = [], []
    for index, period in enumerate(periods):
        period_id, records = month_key(*period), payloads[period]["records"]
        previous_map = previous_maps[period]
        buttons.append(
            f'<button class="month-button{" active" if index == 0 else ""}" '
            f'data-month="{period_id}">{month_label(*period)} '
            f"<strong>{len(records)}</strong></button>"
        )
        rows = []
        for rank, record in enumerate(records, 1):
            previous = previous_map.get(record["code"], [])
            previous_text = "、".join(previous) if previous else "未出现"
            mark_class = "repeat" if previous else "new"
            search = (record["code"] + record["name"] + record["industry"]).lower()
            rows.append(
                f'<tr data-text="{html.escape(search)}">'
                f'<td class="number rank">{rank}</td>'
                f'<td class="code">{html.escape(record["code"])}</td>'
                f'<td class="name">{html.escape(record["name"])}</td>'
                f'<td class="industry">{html.escape(record["industry"])}</td>'
                f'<td class="number market-cap">{html.escape(record["mktcap"])}</td>'
                f'<td class="number count">{record["daily_gain_count"]}</td>'
                f'<td class="number previous-count">{len(previous)}</td>'
                f'<td class="previous"><span class="mark {mark_class}">{html.escape(previous_text)}</span></td>'
                "</tr>"
            )
        max_count = max((record["daily_gain_count"] for record in records), default=0)
        new_count = sum(not previous_map.get(record["code"]) for record in records)
        sections.append(
            f'<section class="month-section{" active" if index == 0 else ""}" '
            f'data-month="{period_id}">'
            f'<div class="metrics"><div><span>当月股票</span><strong>{len(records)}</strong></div>'
            f'<div><span>最高出现次数</span><strong>{max_count}</strong></div>'
            f'<div><span>区间首次出现</span><strong>{new_count}</strong></div></div>'
            '<div class="table-wrap"><table><thead><tr>'
            '<th>序号</th><th>股票代码</th><th>股票简称</th><th>同花顺行业</th>'
            f"<th>总市值</th><th>{month_label(*period)}次数</th>"
            f'<th>此前月份数量</th>'
            f'<th>区间内此前单日涨幅大于{threshold_text}%月份</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></section>'
        )

    document = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{start_key} 至 {end_key} 月度单日涨幅大于{threshold_text}%次数</title><style>
*{{box-sizing:border-box}}body{{margin:0;color:#172033;background:#f4f6f8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}}
header{{background:#17365d;color:white;padding:22px 28px}}h1{{margin:0 0 7px;font-size:24px;letter-spacing:0}}header p{{margin:0;color:#dbe7f5;font-size:14px}}main{{max-width:1500px;margin:auto;padding:20px}}
.toolbar{{display:flex;flex-wrap:wrap;gap:9px;margin-bottom:14px}}input,button{{height:38px;border:1px solid #cbd5e1;border-radius:5px;background:white;padding:0 12px;font-size:14px}}input{{width:min(360px,100%)}}button{{cursor:pointer}}button.active{{background:#17365d;border-color:#17365d;color:white}}
.month-section{{display:none}}.month-section.active{{display:block}}.metrics{{display:grid;grid-template-columns:repeat(3,minmax(120px,220px));gap:10px;margin-bottom:14px}}.metrics div{{background:white;border:1px solid #d8e0e8;padding:13px}}.metrics span{{display:block;color:#64748b;font-size:13px}}.metrics strong{{display:block;margin-top:4px;font-size:22px}}
.table-wrap{{overflow:auto;max-height:calc(100vh - 255px);background:white;border:1px solid #d8e0e8}}table{{width:100%;min-width:1320px;border-collapse:collapse;table-layout:fixed}}th{{position:sticky;top:0;z-index:1;background:#1f4e78;color:white;text-align:left;padding:11px 12px;font-size:13px;white-space:nowrap}}td{{height:48px;padding:10px 12px;border-bottom:1px solid #e6ebf0;font-size:14px;vertical-align:middle}}tbody tr:hover{{background:#f1f6fb}}
th:nth-child(1),.rank{{width:60px}}th:nth-child(2),.code{{width:92px}}th:nth-child(3),.name{{width:112px}}th:nth-child(4),.industry{{width:280px}}th:nth-child(5),.market-cap{{width:130px}}th:nth-child(6),.count{{width:150px}}th:nth-child(7),.previous-count{{width:115px}}th:nth-child(8),.previous{{width:auto}}
.code,.number{{white-space:nowrap;font-variant-numeric:tabular-nums}}.name{{font-weight:600;white-space:nowrap}}.industry{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}.count{{color:#c62828;font-weight:700}}.previous{{min-width:360px}}
.mark{{display:inline-block;max-width:100%;padding:3px 8px;border-radius:4px;white-space:normal;line-height:1.7;font-size:12px;font-weight:600}}.mark.new{{color:#166534;background:#dcfce7}}.mark.repeat{{color:#854d0e;background:#fef9c3}}
@media(max-width:700px){{header{{padding:18px}}main{{padding:12px}}.metrics{{grid-template-columns:repeat(3,1fr)}}.table-wrap{{max-height:calc(100vh - 310px)}}}}
</style></head><body><header><h1>{start_key} 至 {end_key} 月度单日涨幅大于{threshold_text}%次数</h1><p>市值大于{market_text}亿元 · 非北交/非新股/非ST · 2024年之前上市 · 股票当月至少出现1个交易日单日涨幅大于{threshold_text}%即入选 · 按总市值倒排 · 历史月份使用独立缓存</p></header>
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
        save_csv(
            *period,
            payloads[period]["records"],
            previous_maps[period],
            args.threshold,
            args.min_market_cap,
        )
    dashboard = save_dashboard(
        periods, payloads, previous_maps, args.threshold, args.min_market_cap
    )
    enhance_html_sorting.enhance_file(dashboard)
    html_output_cleanup.finalize_html(dashboard)
    print("\n[完成]")
    for period in periods:
        print(f"  {month_label(*period)}：{len(payloads[period]['records'])} 条")
    print(f"[整合网页] {os.path.abspath(dashboard)}")
    print(f"[缓存目录] {os.path.abspath(CACHE_DIR)}")


if __name__ == "__main__":
    main()
