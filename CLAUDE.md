# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

University market intelligence pipeline for the DACH region (Germany, Austria, Switzerland). Crawls news from RSS feeds, HTML domains, and NewsAPI, classifies articles via OpenAI, and generates an HTML digest. Targeted at higher education sector signals relevant to a campus management software vendor (Simovative).

## Running the Pipeline

```bash
# Activate venv and run
source .venv/bin/activate
python main.py
```

Requires `.env` with: `OPENAI_API_KEY`, `OPENAI_MODEL_NAME`, `NEWSAPI_KEY`, `SERPAPI_KEY`.

Output goes to `/tmp/news_digest/`:
- `raw/<date>/` — crawled JSON per source
- `processed/<date>/articles_normalized.json`, `articles_classified.json`
- `processed/<date>/digest_<date>.html` — final HTML digest
- `logs/run_<date>.log`

Playwright must be installed: `playwright install chromium`

## Architecture

Sequential 7-step pipeline orchestrated by `main.py`:

1. **Crawl** (`src/crawlers/`) — three source types run independently with per-source error isolation:
   - `rss_crawler.py` — feedparser-based, date-filtered, includes entry summaries as raw_text
   - `domain_crawler.py` — Playwright-based, discovers article URLs on listing pages then scrapes each article (single browser instance per domain)
   - `newsapi_crawler.py` — REST API via requests, deduplicates by URL across queries
2. **Normalize** (`src/processing/normalizer.py`) — raw dicts → canonical schema with `article_id = sha256(url + date)`, content_hash, region inference from TLD
3. **Deduplicate** (`src/processing/deduplicator.py`) — by article_id then content_hash (skips content-hash dedup for short texts <50 chars)
4. **Classify** (`src/processing/classifier.py`) — one OpenAI call per article, JSON mode, validates against 9-category taxonomy, retries once on failure
5. **Filter + Generate** (`src/digest/html_generator.py`) — applies inclusion rules (relevance ≥ 0.6, confidence ≥ 0.6, excludes "Research News" and "Irrelevant"), renders HTML grouped by taxonomy category

All crawlers return `list[dict]` with a consistent raw article shape (title, url, published_at, raw_text, source_type, source_name).

## Key Configuration

`config.py` contains three source lists: `MANDATORY_DOMAINS` (HTML scraping targets), `RSS_FEEDS`, and `NEWSAPI_QUERIES`. `ARTICLES_PER_DOMAIN` controls the per-domain cap.

## Taxonomy Categories

Leadership & Governance, Regulatory & Policy Changes, Organizational & Structural Changes, Digital Strategy & IT Initiatives, Funding & Investment Signals, Procurement & Tenders, Crisis & Risk Events, Research News, Irrelevant.

## Legacy Files

`market_tools.py`, `scraping_tools.py`, `web_tools.py` at root are the original CrewAI-decorated versions. The active code lives in `src/` with decorators stripped. These root files are kept for reference.
