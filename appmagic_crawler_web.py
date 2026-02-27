"""
AppMagic Crawler Web App
Paste any AppMagic top-charts URL (e.g. .../top-charts/apps?tag=37) → crawl & display.
Run: python appmagic_crawler_web.py
Then open http://127.0.0.1:5000
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path

from urllib.parse import urlparse, parse_qs

from flask import Flask, request, jsonify, send_file, render_template_string, Response

app = Flask(__name__)


def _parse_filters_from_url(url: str) -> dict:
    try:
        qs = parse_qs(urlparse(url).query)
        return {
            k: qs[k][0] if qs.get(k) else ""
            for k in ("aggregation", "country", "store", "tag") if qs.get(k)
        }
    except Exception:
        return {}

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "appmagic_top_charts_output"
OUT_DIR.mkdir(exist_ok=True)


def validate_appmagic_url(url: str) -> bool:
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    return "appmagic.rocks" in url and "top-charts" in url


def crawl_with_playwright(url: str, limit: int = 500) -> tuple[dict, str | None]:
    """Crawl the given AppMagic URL with Playwright. limit = max rows per chart (20-1000)."""
    limit = max(20, min(1000, int(limit) if limit else 500))
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"error": "Playwright not installed. Run: pip install playwright && playwright install chromium"}, None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(
            viewport={"width": 1400, "height": 900},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        )
        try:
            page.goto(url, wait_until="networkidle", timeout=45000)
            page.wait_for_load_state("domcontentloaded")
            page.wait_for_timeout(4000)

            # Scroll to load more rows (if page has virtual scroll / load more)
            for _ in range(min(20, (limit // 50) + 2)):
                prev_count = page.evaluate("() => document.querySelectorAll('table tbody tr, table tr').length")
                page.evaluate("() => { window.scrollBy(0, 800); document.querySelector('.ant-table-body')?.scrollBy?.(0, 800); }")
                page.wait_for_timeout(600)
                new_count = page.evaluate("() => document.querySelectorAll('table tbody tr, table tr').length")
                if new_count <= prev_count:
                    break

            # Screenshot
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = OUT_DIR / f"screenshot_{ts}.png"
            page.screenshot(path=str(screenshot_path), full_page=True)
            ss_path = str(screenshot_path)

            # Extract table rows - broader selectors for different chart types
            raw = page.evaluate("""
() => {
    const result = { sections: [] };
    const tables = document.querySelectorAll('table');
    tables.forEach((tbl, ti) => {
        const rows = tbl.querySelectorAll('tbody tr, tr');
        const section = [];
        rows.forEach((r, ri) => {
            const cells = r.querySelectorAll('td, th');
            const texts = Array.from(cells).map(c => c.innerText.trim()).filter(Boolean);
            if (texts.length >= 2) {
                const rank = parseInt(texts[0], 10) || ri + 1;
                const app = texts[1] || texts[0];
                const publisher = texts[2] || '';
                const metric = texts[texts.length - 1] || '';
                if (app && app.length > 1) section.push({ rank, app, publisher, metric });
            }
        });
        if (section.length) result.sections.push(section);
    });
    return result;
}
            """)

            data = {
                "meta": {
                    "source": url,
                    "crawled_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
                    "screenshot": screenshot_path.name,
                    "filters": {**_parse_filters_from_url(url), "limit_per_chart": limit},
                },
                "top_free_downloads": [],
                "top_grossing_revenue": [],
            }

            for section in raw.get("sections", [])[:2]:
                if not section:
                    continue
                sliced = section[:limit]
                first_metric = (sliced[0] or {}).get("metric", "") if sliced else ""
                if "$" in first_metric or "revenue" in first_metric.lower():
                    data["top_grossing_revenue"] = [
                        {"rank": r.get("rank", i + 1), "app": r.get("app", ""), "publisher": r.get("publisher", ""),
                         "metric": r.get("metric", ""), "metric_type": "revenue"}
                        for i, r in enumerate(sliced)
                    ]
                else:
                    data["top_free_downloads"] = [
                        {"rank": r.get("rank", i + 1), "app": r.get("app", ""), "publisher": r.get("publisher", ""),
                         "metric": r.get("metric", ""), "metric_type": "downloads"}
                        for i, r in enumerate(sliced)
                    ]

            if data["top_free_downloads"] and not data["top_grossing_revenue"]:
                data["top_grossing_revenue"] = data["top_free_downloads"][:limit]
            elif data["top_grossing_revenue"] and not data["top_free_downloads"]:
                data["top_free_downloads"] = data["top_grossing_revenue"][:limit]

            browser.close()
            return data, ss_path
        except Exception as e:
            browser.close()
            return {"error": str(e)}, None


def parse_metric(val: str) -> str:
    if not val:
        return ""
    return re.sub(r"[^\d]", "", val)


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/crawl", methods=["POST"])
def crawl():
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or request.form.get("url") or "").strip()
    limit = body.get("limit", 500)
    if not validate_appmagic_url(url):
        return jsonify({"error": "Invalid URL. Must be an AppMagic top-charts link (appmagic.rocks/.../top-charts/...)"}), 400
    data, screenshot_path = crawl_with_playwright(url, limit=limit)
    if "error" in data:
        return jsonify(data), 500
    return jsonify(data)


@app.route("/screenshot/<name>")
def screenshot(name):
    path = OUT_DIR / name
    if not path.exists() or path.resolve().parent != OUT_DIR.resolve():
        return "Not found", 404
    return send_file(path, mimetype="image/png")


@app.route("/export/<fmt>", methods=["POST"])
def export(fmt):
    body = request.get_json(silent=True) or {}
    data = body.get("data")
    if not data:
        return jsonify({"error": "No data to export"}), 400

    if fmt == "json":
        buf = io.BytesIO(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        return send_file(buf, mimetype="application/json", as_attachment=True, download_name="appmagic_crawled.json")

    if fmt == "csv":
        rows = []
        for chart, arr_key, metric_type in [
            ("top_free_downloads", "top_free_downloads", "downloads"),
            ("top_grossing_revenue", "top_grossing_revenue", "revenue"),
        ]:
            for r in data.get(arr_key, []):
                rows.append({
                    "chart": chart, "rank": r.get("rank"), "app": r.get("app"),
                    "publisher": r.get("publisher"), "metric": parse_metric(r.get("metric", "")),
                    "metric_type": metric_type,
                })
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=["chart", "rank", "app", "publisher", "metric", "metric_type"])
        w.writeheader()
        w.writerows(rows)
        return Response(
            buf.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=appmagic_crawled.csv"},
        )
    return jsonify({"error": "Unknown format"}), 400


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>AppMagic Crawler</title>
  <style>
    * { box-sizing: border-box; }
    body {
      font-family: 'Segoe UI', system-ui, sans-serif;
      background: linear-gradient(135deg, #0d0d12 0%, #1a1a24 100%);
      color: #e8e8ec;
      margin: 0;
      min-height: 100vh;
      padding: 2rem;
    }
    .container { max-width: 960px; margin: 0 auto; }
    h1 { font-size: 1.6rem; color: #e84393; margin-bottom: 0.25rem; }
    .sub { font-size: 0.9rem; color: #888; margin-bottom: 1rem; }
    .input-row {
      display: flex;
      gap: 0.75rem;
      margin-bottom: 1rem;
    }
    input[type="url"] {
      flex: 1;
      padding: 0.75rem 1rem;
      border: 1px solid rgba(232,67,147,0.3);
      border-radius: 8px;
      background: #1a1a1f;
      color: #fff;
      font-size: 0.95rem;
    }
    input[type="url"]::placeholder { color: #666; }
    input[type="url"]:focus { outline: none; border-color: #e84393; box-shadow: 0 0 0 2px rgba(232,67,147,0.2); }
    button {
      padding: 0.75rem 1.5rem;
      background: #e84393;
      color: #fff;
      border: none;
      border-radius: 8px;
      font-weight: 600;
      cursor: pointer;
      font-size: 0.95rem;
      white-space: nowrap;
    }
    button:hover { filter: brightness(1.1); }
    button:disabled { opacity: 0.6; cursor: not-allowed; }
    .filters {
      display: flex;
      flex-wrap: wrap;
      gap: 1rem;
      margin-bottom: 1rem;
      padding: 1rem;
      background: #1a1a1f;
      border: 1px solid rgba(232,67,147,0.2);
      border-radius: 8px;
    }
    .filter-group { display: flex; flex-direction: column; gap: 0.35rem; }
    .filter-group label { font-size: 0.75rem; color: #888; text-transform: uppercase; }
    .filter-group select {
      padding: 0.5rem 0.75rem;
      border-radius: 6px;
      border: 1px solid rgba(255,255,255,0.15);
      background: #0d0d12;
      color: #e8e8ec;
      font-size: 0.9rem;
      min-width: 140px;
    }
    .filter-group select:focus { outline: none; border-color: #e84393; }
    .meta-badge { font-size: 0.8rem; color: #888; background: #252530; padding: 0.25rem 0.5rem; border-radius: 4px; margin-top: 0.5rem; }
    .status { font-size: 0.9rem; color: #888; margin-bottom: 1rem; min-height: 1.2em; }
    .error { color: #ff6b6b; }
    .results { display: none; }
    .results.visible { display: block; }
    .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1.5rem; margin-top: 1rem; }
    @media (max-width: 700px) { .grid { grid-template-columns: 1fr; } }
    .panel {
      background: #1a1a1f;
      border: 1px solid rgba(232,67,147,0.2);
      border-radius: 10px;
      padding: 1rem 1.25rem;
    }
    .panel h2 { font-size: 1rem; color: #e84393; margin: 0 0 0.75rem; }
    .panel#topFreePanel, .panel#topGrossingPanel { max-height: 70vh; overflow-y: auto; }
    .row {
      display: grid;
      grid-template-columns: 36px 1fr 100px;
      gap: 0.5rem;
      padding: 0.4rem 0;
      border-bottom: 1px solid rgba(255,255,255,0.06);
      font-size: 0.9rem;
    }
    .row .rank { color: #888; font-weight: 600; }
    .row .metric { color: #888; font-size: 0.8rem; text-align: right; }
    .screenshot { max-width: 100%; border-radius: 8px; margin-top: 1rem; border: 1px solid rgba(255,255,255,0.1); }
    .actions { margin-top: 1rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
    .actions button { background: #333; }
    .actions button.primary { background: #e84393; }
  </style>
</head>
<body>
  <div class="container">
    <h1>AppMagic Crawler</h1>
    <p class="sub">Paste any AppMagic top-charts link below and click Crawl. No login, no bookmark — just paste and fetch.</p>

    <div class="input-row">
      <input type="url" id="urlInput" placeholder="Paste AppMagic URL here (e.g. https://appmagic.rocks/top-charts/apps?tag=37)" value="">
      <button id="crawlBtn">Crawl</button>
    </div>
    <div class="filters">
      <div class="filter-group">
        <label>Time Period</label>
        <select id="aggregation">
          <option value="day">Day</option>
          <option value="week">Week</option>
          <option value="month">Month</option>
          <option value="quarter" selected>Quarter</option>
          <option value="year">Year</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Country</label>
        <select id="country">
          <option value="W1">Worldwide</option>
          <option value="US,W1">US + Worldwide</option>
          <option value="US">United States</option>
          <option value="GB">United Kingdom</option>
          <option value="DE">Germany</option>
          <option value="FR">France</option>
          <option value="JP">Japan</option>
          <option value="CN">China</option>
          <option value="KR">South Korea</option>
          <option value="IN">India</option>
          <option value="BR">Brazil</option>
          <option value="CA">Canada</option>
          <option value="AU">Australia</option>
          <option value="RU">Russia</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Store</label>
        <select id="store">
          <option value="">All Stores</option>
          <option value="1">Android (Google Play)</option>
          <option value="4" selected>iOS (App Store)</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Results per chart</label>
        <select id="limit">
          <option value="20">20</option>
          <option value="50">50</option>
          <option value="100">100</option>
          <option value="200">200</option>
          <option value="500" selected>500</option>
          <option value="1000">1000</option>
        </select>
      </div>
    </div>
    <div class="status" id="status"></div>
    <div class="results" id="results">
      <div id="metaBadge" class="meta-badge" style="display:none;"></div>
      <div class="grid">
        <div class="panel" id="topFreePanel">
          <h2>Top Free (Downloads)</h2>
          <div id="topFree"></div>
        </div>
        <div class="panel" id="topGrossingPanel">
          <h2>Top Grossing (Revenue)</h2>
          <div id="topGrossing"></div>
        </div>
      </div>
      <div class="panel" style="margin-top: 1rem;">
        <h2>Screenshot</h2>
        <img id="screenshot" class="screenshot" alt="Screenshot" style="display:none;">
      </div>
      <div class="actions">
        <button class="primary" id="exportJson">Export JSON</button>
        <button id="exportCsv">Export CSV</button>
      </div>
    </div>
  </div>

  <script>
    const urlInput = document.getElementById('urlInput');
    const crawlBtn = document.getElementById('crawlBtn');
    const status = document.getElementById('status');
    const results = document.getElementById('results');
    const metaBadge = document.getElementById('metaBadge');
    const aggregationEl = document.getElementById('aggregation');
    const countryEl = document.getElementById('country');
    const storeEl = document.getElementById('store');
    const limitEl = document.getElementById('limit');
    let lastData = null;

    function buildCrawlUrl() {
      let url = urlInput.value.trim();
      if (!url || !url.includes('appmagic.rocks') || !url.includes('top-charts')) return null;
      try {
        const u = new URL(url);
        u.searchParams.set('aggregation', aggregationEl.value || 'month');
        u.searchParams.set('country', countryEl.value || 'W1');
        if (storeEl.value) u.searchParams.set('store', storeEl.value);
        return u.toString();
      } catch { return url; }
    }

    function parseUrlAndApplyFilters() {
      const url = urlInput.value.trim();
      if (!url) return;
      try {
        const u = new URL(url);
        const agg = u.searchParams.get('aggregation');
        if (agg) aggregationEl.value = agg;
        const c = u.searchParams.get('country');
        if (c) countryEl.value = c.trim();
        const s = u.searchParams.get('store');
        if (s) storeEl.value = s; else storeEl.value = '';
      } catch (_) {}
    }
    urlInput.addEventListener('blur', parseUrlAndApplyFilters);

    crawlBtn.onclick = async () => {
      const url = buildCrawlUrl();
      if (!url) {
        status.textContent = 'Please paste a valid AppMagic top-charts URL (appmagic.rocks/.../top-charts/...)';
        status.classList.add('error');
        return;
      }
      const agg = aggregationEl.options[aggregationEl.selectedIndex].text;
      const country = countryEl.options[countryEl.selectedIndex].text;
      const store = storeEl.options[storeEl.selectedIndex].text;
      status.textContent = 'Crawling... ' + agg + ' · ' + country + ' · ' + store + ' (10–15 sec)';
      status.classList.remove('error');
      crawlBtn.disabled = true;
      try {
        const res = await fetch('/crawl', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url, limit: parseInt(limitEl.value, 10) || 500 })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Crawl failed');
        lastData = data;
        render(data);
        results.classList.add('visible');
        metaBadge.textContent = 'Time: ' + agg + ' · Country: ' + country + ' · Store: ' + store;
        metaBadge.style.display = 'block';
        status.textContent = 'Done. Fetched ' + ((data.top_free_downloads?.length || 0) + (data.top_grossing_revenue?.length || 0)) + ' apps.';
        status.classList.remove('error');
      } catch (e) {
        status.textContent = 'Error: ' + e.message;
        status.classList.add('error');
      }
      crawlBtn.disabled = false;
    };

    function render(data) {
      const renderList = (el, items) => {
        el.innerHTML = (items || []).map(r =>
          `<div class="row"><span class="rank">${r.rank}</span><span>${escapeHtml(r.app)}</span><span class="metric">${escapeHtml(r.metric)}</span></div>`
        ).join('');
      };
      renderList(document.getElementById('topFree'), data.top_free_downloads);
      renderList(document.getElementById('topGrossing'), data.top_grossing_revenue);

      const ss = document.getElementById('screenshot');
      if (data.meta?.screenshot) {
        ss.src = '/screenshot/' + encodeURIComponent(data.meta.screenshot);
        ss.style.display = 'block';
      } else { ss.style.display = 'none'; }
    }

    function escapeHtml(s) { return (s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }

    document.getElementById('exportJson').onclick = () => {
      if (!lastData) return;
      const a = document.createElement('a');
      a.href = 'data:application/json;charset=utf-8,' + encodeURIComponent(JSON.stringify(lastData, null, 2));
      a.download = 'appmagic_crawled.json';
      a.click();
    };

    document.getElementById('exportCsv').onclick = async () => {
      if (!lastData) return;
      const res = await fetch('/export/csv', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: lastData }) });
      const blob = await res.blob();
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = 'appmagic_crawled.csv';
      a.click();
    };
  </script>
</body>
</html>"""


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=port, debug=debug)
