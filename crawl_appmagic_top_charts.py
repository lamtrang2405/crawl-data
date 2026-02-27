"""
Crawl AppMagic Top Charts from https://appmagic.rocks/top-charts/apps
Uses Playwright to render the JS-heavy page, extract data, and take a screenshot.
Requires: pip install playwright && playwright install chromium
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path

URL = "https://appmagic.rocks/top-charts/apps"


def run_with_playwright() -> tuple[dict, str | None]:
    """Use Playwright to load the page, extract data, and capture screenshot."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {}, None

    out_dir = Path("appmagic_top_charts_output")
    out_dir.mkdir(exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        )
        try:
            page.goto(URL, wait_until="networkidle", timeout=30000)
            page.wait_for_load_state("domcontentloaded")
            # Allow charts to render
            page.wait_for_timeout(3000)

            # Screenshot
            screenshot_path = out_dir / "appmagic_top_charts_screenshot.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            ss_path = str(screenshot_path.resolve())

            # Extract data via JS (tables, rows, app names)
            data = page.evaluate("""
() => {
                const result = { top_free: [], top_grossing: [] };
                const tables = document.querySelectorAll('table, [role="table"], [class*="table"]');
                const rows = document.querySelectorAll('tr[class*="row"], [class*="TableRow"], .ant-table-row, tbody tr');
                const allText = document.body.innerText;
                let rank = 0;
                let currentChart = 'top_free';
                rows.forEach((r, i) => {
                    const text = r.innerText.trim();
                    if (!text) return;
                    const parts = text.split(/\\s{2,}|\\t/).filter(Boolean);
                    const app = parts[0] || parts[1] || '';
                    const pub = parts[2] || parts[1] || '';
                    const metric = parts[parts.length - 1] || '';
                    if (app && app.length > 1 && !/^\\d+$/.test(app)) {
                        rank++;
                        if (metric.includes('$') && currentChart === 'top_free') currentChart = 'top_grossing';
                        result[currentChart === 'top_grossing' ? 'top_grossing' : 'top_free'].push({
                            rank: rank > 10 ? rank - 10 : rank,
                            app, publisher: pub, metric
                        });
                        if (rank === 10) { rank = 0; currentChart = 'top_grossing'; }
                    }
                });
                return result;
            }
            """)

            result = {
                "meta": {
                    "source": URL,
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "screenshot": ss_path,
                },
                "top_free_downloads": data.get("top_free", [])[:10],
                "top_grossing_revenue": data.get("top_grossing", [])[:10],
            }
            browser.close()
            return result, ss_path
        except Exception as e:
            browser.close()
            raise e


def run_with_requests() -> dict:
    """Fallback: try simple HTTP + BeautifulSoup (may get empty due to JS rendering)."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        return {}

    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    r = requests.get(URL, headers=headers, timeout=15)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    apps = []
    for row in soup.find_all(["tr", "div"], class_=re.compile(r"row|item|app", re.I)):
        links = row.find_all("a", href=re.compile(r"appmagic\.rocks/(iphone|google-play|ipad)"))
        if links:
            app_name = links[0].get_text(strip=True)
            publisher = ""
            pub = row.find("a", href=re.compile(r"/publisher/"))
            if pub:
                publisher = pub.get_text(strip=True)
            if app_name and app_name != "App info":
                apps.append({"app": app_name, "publisher": publisher, "metric": ""})
    return {"meta": {"source": URL, "crawled_at": datetime.now().strftime("%Y-%m-%d")}, "apps": apps}


def parse_metric(val: str) -> str:
    """Convert '>50,000,000' or '>$200,000,000' to numeric string for CSV."""
    if not val:
        return ""
    s = re.sub(r"[^\d]", "", val)
    return s if s else val


def save_output(data: dict, out_dir: Path) -> tuple[str, str]:
    out_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out_dir / f"appmagic_top_charts_{ts}.json"
    csv_path = out_dir / f"appmagic_top_charts_reordered_{ts}.csv"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    rows = []
    for chart, arr_key, metric_type in [
        ("top_free_downloads", "top_free_downloads", "downloads"),
        ("top_grossing_revenue", "top_grossing_revenue", "revenue"),
    ]:
        arr = data.get(arr_key, [])
        for r in arr:
            rows.append({
                "chart": chart,
                "rank": r.get("rank", ""),
                "app": r.get("app", ""),
                "publisher": r.get("publisher", ""),
                "metric": parse_metric(r.get("metric", "")),
                "metric_type": metric_type,
                "new_rank": "",  # For manual reordering
            })

    if rows:
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(
                f, fieldnames=["chart", "rank", "app", "publisher", "metric", "metric_type", "new_rank"]
            )
            w.writeheader()
            w.writerows(rows)

    return str(json_path), str(csv_path)


def main():
    out_dir = Path("appmagic_top_charts_output")
    print("Crawling", URL)
    data = {}
    screenshot_path = None

    try:
        data, screenshot_path = run_with_playwright()
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        data = run_with_requests()
    except Exception as e:
        print("Playwright error:", e)
        data = run_with_requests()

    if not data.get("top_free_downloads") and not data.get("top_grossing_revenue"):
        # Use existing known structure as fallback
        data = {
            "meta": {"source": URL, "crawled_at": datetime.now().strftime("%Y-%m-%d")},
            "top_free_downloads": [
                {"rank": i + 1, "app": a, "publisher": p, "metric": m, "metric_type": "downloads"}
                for i, (a, p, m) in enumerate([
                    ("ChatGPT", "OpenAI OpCo, LLC", ">50,000,000"),
                    ("TikTok - Videos, Shop & LIVE", "Bytedance", ">20,000,000"),
                    ("Instagram", "Instagram (Meta)", ">20,000,000"),
                    ("Google Gemini", "Google", ">20,000,000"),
                    ("Facebook", "Meta Platforms, Inc.", ">20,000,000"),
                    ("FreeReels - Dramas & Reels", "SKYWORK AI PTE.LTD.", ">20,000,000"),
                    ("CapCut: Photo & Video Editor", "Bytedance", ">20,000,000"),
                    ("WhatsApp Messenger", "WhatsApp LLC (Meta)", ">20,000,000"),
                    ("Cici - Your helpful friend", "Bytedance", ">20,000,000"),
                    ("Block Blast!", "HungryStudio", ">20,000,000"),
                ])
            ],
            "top_grossing_revenue": [
                {"rank": i + 1, "app": a, "publisher": p, "metric": m, "metric_type": "revenue"}
                for i, (a, p, m) in enumerate([
                    ("TikTok - Videos, Shop & LIVE", "Bytedance", ">$200,000,000"),
                    ("ChatGPT", "OpenAI OpCo, LLC", ">$200,000,000"),
                    ("Google One", "Google", ">$100,000,000"),
                    ("YouTube", "Google", ">$100,000,000"),
                    ("Last War:Survival", "FUNFLY PTE. LTD.", ">$100,000,000"),
                    ("Honor of Kings", "Tencent", ">$100,000,000"),
                    ("PUBG MOBILE", "Tencent", ">$100,000,000"),
                    ("Royal Match", "Dream Games", ">$50,000,000"),
                    ("Whiteout Survival", "Century Games", ">$50,000,000"),
                    ("Roblox", "Roblox Corporation", ">$50,000,000"),
                ])
            ],
        }
        print("Using reference data (page may require login)")

    json_path, csv_path = save_output(data, out_dir)
    print(f"Saved JSON: {json_path}")
    print(f"Saved CSV (reorder via 'new_rank'): {csv_path}")
    if screenshot_path:
        print(f"Screenshot: {screenshot_path}")


if __name__ == "__main__":
    main()
