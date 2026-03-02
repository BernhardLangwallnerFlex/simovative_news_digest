"""NewsAPI.org crawler."""

import logging
import os
from datetime import datetime, timedelta

import requests

logger = logging.getLogger(__name__)

NEWSAPI_BASE = "https://newsapi.org/v2/everything"


def fetch_newsapi(queries: list[str], days_back: int = 4) -> list[dict]:
    """Fetch articles from NewsAPI for the given queries.

    Args:
        queries: List of search query strings.
        days_back: Number of days to look back from today.

    Returns:
        List of raw article dicts, deduplicated by URL across queries.
    """
    api_key = os.getenv("NEWSAPI_KEY")
    if not api_key:
        logger.error("NEWSAPI_KEY not set — skipping NewsAPI")
        return []

    from_date = (datetime.utcnow() - timedelta(days=days_back)).strftime("%Y-%m-%d")
    all_articles: list[dict] = []
    seen_urls: set[str] = set()

    for query in queries:
        try:
            params = {
                "q": query,
                "language": "de",
                "sortBy": "publishedAt",
                "from": from_date,
                "pageSize": 100,
                "apiKey": api_key,
            }
            resp = requests.get(NEWSAPI_BASE, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()

            if data.get("status") != "ok":
                logger.warning("NewsAPI error for query '%s': %s", query, data.get("message"))
                continue

            count = 0
            for article in data.get("articles", []):
                url = article.get("url", "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)

                description = article.get("description") or ""
                content = article.get("content") or ""
                raw_text = f"{description}\n\n{content}".strip()

                all_articles.append({
                    "title": article.get("title", "").strip(),
                    "url": url,
                    "published_at": article.get("publishedAt", ""),
                    "raw_text": raw_text,
                    "author": article.get("author"),
                    "source_type": "api",
                    "source_name": article.get("source", {}).get("name", ""),
                    "crawl_error": None,
                })
                count += 1

            logger.info("NewsAPI query '%s': %d articles", query, count)

        except Exception as e:
            logger.error("NewsAPI request failed for query '%s': %s", query, e)

    logger.info("NewsAPI total: %d articles from %d queries", len(all_articles), len(queries))
    return all_articles
