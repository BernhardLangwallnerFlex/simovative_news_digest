# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

University market intelligence pipeline for the DACH region (Germany, Austria, Switzerland). Crawls news from RSS feeds, HTML domains, and NewsAPI, classifies articles via OpenAI, and generates an HTML digest. Targeted at higher education sector signals relevant to a campus management software vendor (Simovative).

## Running the Pipeline

```bash
# Local: activate venv and run
source .venv/bin/activate
python main.py
```

Requires `.env` with: `OPENAI_API_KEY`, `OPENAI_MODEL_NAME`, `NEWSAPI_KEY`, `SERPAPI_KEY`, `AZURE_STORAGE_CONNECTION_STRING`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_RECIPIENTS`, `SEND_GRID_API_KEY`.

Output goes to `/tmp/news_digest/`:
- `raw/<date>/` — crawled JSON per source
- `processed/<date>/articles_normalized.json`, `articles_classified.json`
- `processed/<date>/digest_<date>.html` — final HTML digest
- `logs/run_<date>.log`

Playwright must be installed: `playwright install chromium`

## Azure Deployment

Runs as a scheduled **Azure Container Apps Job** in resource group `simovativedigest` (germanywestcentral). Fires every Tuesday and Friday at 10:00 CEST (cron `0 8 * * 2,5` UTC).

**Resources:**
- Storage Account: `simovativedigest` — blob containers `news-digest-history` (article history for cross-run dedup)
- Container Registry: `simovativedigestacr` (Basic SKU, image `news-digest:v1`)
- Log Analytics: `simovativedigest-logs`
- Container Apps Environment: `simovativedigest-env` (Consumption plan, scale-to-zero)
- Job: `news-digest-job` (2 vCPU, 4 GiB memory, 1h timeout, 1 retry)

**Infrastructure files:**
- `infra/main.bicep` — Bicep template for all resources
- `infra/main.bicepparam` — secret parameters (gitignored)
- `infra/deploy.sh` — deployment script (reads secrets from `.env`)

**Deploy:**
```bash
./infra/deploy.sh              # full deploy (ACR + image + infra)
./infra/deploy.sh --image-only # rebuild and push image only
./infra/deploy.sh --infra-only # update Bicep resources only
```

**Monitor:**
```bash
az containerapp job execution list --name news-digest-job -g simovativedigest -o table
az containerapp job start --name news-digest-job -g simovativedigest  # manual trigger
```

## Architecture

Sequential pipeline orchestrated by `main.py`:

1. **Crawl** (`src/crawlers/`) — three source types run independently with per-source error isolation:
   - `rss_crawler.py` — feedparser-based, date-filtered, includes entry summaries as raw_text
   - `university_domain_crawler.py` — Playwright-based, pattern-matched link discovery (from `news_article_patterns.json`) with sentence-transformer fallback, shared browser instance across all domains
   - `newsapi_crawler.py` — REST API via requests, deduplicates by URL across queries
2. **Normalize** (`src/processing/normalizer.py`) — raw dicts → canonical schema with `article_id = sha256(url + date)`, content_hash, region inference from TLD
3. **Deduplicate** (`src/processing/deduplicator.py`) — by article_id then content_hash (skips content-hash dedup for short texts <50 chars)
4. **History filter** (`src/processing/history.py`) — loads previously seen article IDs from Azure Blob Storage, skips already-processed articles
5. **Classify** (`src/processing/classifier.py`) — one OpenAI call per article, JSON mode, validates against 9-category taxonomy, retries once on failure
6. **Near-dedup** (`src/processing/near_dedup.py`) — LLM-based detection of agency wire duplicates, merges into groups with "also reported by" links
7. **Filter + Generate** (`src/digest/html_generator.py`) — applies inclusion rules (relevance >= 0.6, confidence >= 0.6, excludes "Research News" and "Irrelevant"), renders HTML grouped by taxonomy category
8. **Email delivery** (`src/delivery/email_sender.py`) — sends HTML digest via SMTP to configured recipients

All crawlers return `list[dict]` with a consistent raw article shape (title, url, published_at, raw_text, source_type, source_name).

## Key Configuration

`config.py` contains three source lists: `MANDATORY_DOMAINS` (HTML scraping targets), `RSS_FEEDS`, and `NEWSAPI_QUERIES`. `ARTICLES_PER_DOMAIN` controls the per-domain cap.

## Taxonomy Categories

Leadership & Governance, Regulatory & Policy Changes, Organizational & Structural Changes, Digital Strategy & IT Initiatives, Funding & Investment Signals, Procurement & Tenders, Crisis & Risk Events, Research News, Irrelevant.

## Legacy Files

`market_tools.py`, `scraping_tools.py`, `web_tools.py` at root are the original CrewAI-decorated versions. The active code lives in `src/` with decorators stripped. These root files are kept for reference.
