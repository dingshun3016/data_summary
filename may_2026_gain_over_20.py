#!/usr/bin/python
# -*- coding: utf-8 -*-
"""问财“月度涨幅大于20%”抓取公共模块。"""

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


BASE_URL = "https://www.iwencai.com/screener/result?querytype=stock&w="
CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def build_query(label):
    return (
        f"{label}涨幅大于20%的股票，{label}涨跌幅，"
        "市值，行业板块，上市时间，"
        "非北交，非新股，非ST，"
        "市值大于200亿，上市时间在2024年之前"
    )


def build_url(query):
    return BASE_URL + quote(query)


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


def parse_listing_date(text):
    digits = re.sub(r"\D", "", text or "")
    if len(digits) < 8:
        return None
    try:
        return datetime.strptime(digits[:8], "%Y%m%d")
    except ValueError:
        return None


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
    screenshot = os.path.abspath(f"iwencai_gain_failed_{timestamp}.png")
    html_file = os.path.abspath(f"iwencai_gain_failed_{timestamp}.html")
    try:
        driver.save_screenshot(screenshot)
        with open(html_file, "w", encoding="utf-8") as file:
            file.write(driver.page_source)
        print(f"[诊断] 页面标题：{driver.title}")
        print(f"[诊断] 当前 URL：{driver.current_url}")
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


def find_column(headers, keyword_sets):
    for keywords in keyword_sets:
        for index, header in enumerate(headers):
            compact = re.sub(r"\s+", "", header).lower()
            if all(keyword.lower() in compact for keyword in keywords):
                return index
    return None


def infer_columns_from_row(cells):
    code_index = next(
        (index for index, cell in enumerate(cells) if re.fullmatch(r"\d{6}", cell.strip())),
        None,
    )
    if code_index is None:
        return None
    industry_index = next(
        (
            index for index in range(code_index + 2, len(cells))
            if cells[index].count("-") >= 2
            and re.search(r"[\u4e00-\u9fff]", cells[index])
            and index >= 2
            and "亿" in cells[index - 1]
            and parse_number(cells[index - 2]) is not None
        ),
        None,
    )
    market_index = industry_index - 1 if industry_index is not None else None
    gain_index = industry_index - 2 if industry_index is not None else None
    listing_index = next(
        (
            index
            for index in range((industry_index or code_index) + 1, len(cells))
            if parse_listing_date(cells[index]) is not None
        ),
        None,
    )
    if None in (market_index, industry_index, listing_index, gain_index):
        return None
    return {
        "code": code_index,
        "name": code_index + 1,
        "gain": gain_index,
        "market_cap": market_index,
        "industry": industry_index,
        "listing_date": listing_index,
    }


def get_page_records(driver):
    table = get_page_table(driver) or {}
    headers = table.get("headers") or []
    rows = table.get("rows") or []
    indexes = {
        "code": find_column(headers, [["股票代码"]]),
        "name": find_column(headers, [["股票简称"]]),
        "gain": find_column(headers, [["涨跌幅"], ["区间涨幅"]]),
        "market_cap": find_column(headers, [["总市值"], ["市值"]]),
        "industry": find_column(headers, [["同花顺行业"], ["行业"]]),
        "listing_date": find_column(headers, [["上市日期"], ["上市时间"]]),
    }
    if any(index is None for index in indexes.values()):
        indexes = infer_columns_from_row(rows[0]) if rows else None
    if not indexes:
        return []

    required_index = max(indexes.values())
    records = []
    for cells in rows:
        if len(cells) <= required_index:
            continue
        code = cells[indexes["code"]].strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        records.append(
            {
                "code": code,
                "name": cells[indexes["name"]].strip(),
                "gain": cells[indexes["gain"]].strip(),
                "mktcap": cells[indexes["market_cap"]].strip(),
                "industry": cells[indexes["industry"]].strip(),
                "listing_date": cells[indexes["listing_date"]].strip(),
            }
        )
    return records


def pagination_info(driver):
    return driver.execute_script(
        """
        const ul = document.querySelector('ul.pcwencai-pagination');
        if (!ul) return {nextDisabled: true};
        const items = Array.from(ul.querySelectorAll('li'));
        const legacyNext = items.find(li => ['下页', '下一页'].includes(li.innerText.trim()));
        const next = document.querySelector(
          '.pcwencai-pagination-wrap .next-link, .paginate-cus-container .next-link'
        ) || legacyNext;
        const nextDisabled = !next || next.className.includes('disabled') ||
          next.hasAttribute('disabled') || next.getAttribute('aria-disabled') === 'true';
        return {nextDisabled};
        """
    )


def click_next(driver, target_page):
    driver.execute_script("window.scrollTo(0,document.body.scrollHeight);")
    time.sleep(0.8)
    result = driver.execute_script(
        f"""
        const ul = document.querySelector('ul.pcwencai-pagination');
        if (!ul) return 'no pagination';
        const items = Array.from(ul.querySelectorAll('li'));
        for (const li of items) {{
          if (li.innerText.trim() === '{target_page}') {{
            const target = li.querySelector('a,span,button') || li;
            target.scrollIntoView({{block: 'center'}});
            target.click();
            return 'clicked page {target_page}';
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
        return 'next page not found';
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


def scrape_all_pages(driver, url, query=None, label=None):
    label = label or "当前月份"
    if query:
        print(f"\n[问财条件] {query}")
    print(f"[开始抓取] {label}")
    driver.get(url)
    time.sleep(6)
    if not wait_for_data(driver):
        print_page_diagnostics(driver)
        raise RuntimeError(f"{label} 问财表格未加载")
    headers = (get_page_table(driver) or {}).get("headers") or []
    if headers:
        print(f"  [列识别] 已按表头识别 {len(headers)} 列")
    else:
        print("  [列识别] 表头与数据分离，已按涨跌幅/总市值/行业结构识别")
    if not get_page_records(driver):
        print_page_diagnostics(driver)
        raise RuntimeError(f"{label} 未能识别涨跌幅、总市值、行业或上市日期列")

    all_records = []
    seen = set()
    page = 1
    while True:
        records = get_page_records(driver)
        new_records = [record for record in records if record["code"] not in seen]
        for record in new_records:
            seen.add(record["code"])
            all_records.append(record)
        print(
            f"  第 {page} 页：本页 {len(records)} 条，"
            f"新增 {len(new_records)} 条，累计 {len(all_records)} 条"
        )
        if pagination_info(driver)["nextDisabled"]:
            break
        old_code = records[0]["code"] if records else ""
        if not click_next(driver, page + 1):
            break
        if not wait_for_page_change(driver, old_code):
            break
        page += 1
    return all_records


def validate_records(records):
    valid = []
    rejected = []
    for record in records:
        gain_pct = parse_number(record.get("gain", ""))
        market_cap_yi = parse_market_cap_yi(record.get("mktcap", ""))
        listing_date = parse_listing_date(record.get("listing_date", ""))
        code = record.get("code", "")
        name = record.get("name", "")
        reasons = []
        if gain_pct is None or gain_pct <= 20:
            reasons.append("月涨幅不大于20%")
        if market_cap_yi is None or market_cap_yi <= 200:
            reasons.append("总市值不大于200亿")
        if listing_date is None or listing_date >= datetime(2024, 1, 1):
            reasons.append("上市时间不早于2024年")
        if code.startswith(("4", "8", "920")):
            reasons.append("北交所股票")
        if "ST" in name.upper():
            reasons.append("ST股票")
        if reasons:
            rejected.append({"record": record, "reasons": reasons})
            continue

        normalized = dict(record)
        normalized.update(
            {
                "gain_pct": gain_pct,
                "market_cap_yi": market_cap_yi,
                "listing_date_parsed": listing_date.strftime("%Y-%m-%d"),
            }
        )
        valid.append(normalized)
    valid.sort(key=lambda record: record["market_cap_yi"], reverse=True)
    return valid, rejected
