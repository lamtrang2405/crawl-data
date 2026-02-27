# AppMagic Crawler

Extract top charts data from [AppMagic](https://appmagic.rocks/top-charts/apps) — no server, no login.

## Live (GitHub Pages)

**https://lamtrang2405.github.io/crawl-data/**

## Quick start

1. Open [appmagic_bookmarklet.html](docs/appmagic_bookmarklet.html) and drag the bookmarklet to your bookmarks bar
2. Go to [appmagic.rocks/top-charts/apps](https://appmagic.rocks/top-charts/apps)
3. Click the bookmarklet → extract data, download JSON or CSV

## Local (optional)

```bash
pip install -r appmagic_top_charts_requirements.txt
playwright install chromium
python appmagic_crawler_web.py
```

Then open http://127.0.0.1:5000 for the Crawl button.
