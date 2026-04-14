"""NewsAPI.org crawler."""

import logging
import os
import time
from datetime import datetime, timedelta

import requests
from requests.exceptions import ConnectionError, Timeout

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

    _MAX_RETRIES = 3
    _BACKOFF_BASE = 2  # seconds

    for query in queries:
        for attempt in range(1, _MAX_RETRIES + 1):
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
                    break  # API-level error, no point retrying

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
                break  # success

            except (ConnectionError, Timeout, OSError) as e:
                if attempt < _MAX_RETRIES:
                    wait = _BACKOFF_BASE ** attempt
                    logger.warning(
                        "NewsAPI transient error for '%s' (attempt %d/%d): %s — retrying in %ds",
                        query, attempt, _MAX_RETRIES, e, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        "NewsAPI failed for query '%s' after %d attempts: %s",
                        query, _MAX_RETRIES, e,
                    )

            except Exception as e:
                logger.error("NewsAPI request failed for query '%s': %s", query, e)
                break  # non-transient error, don't retry

        time.sleep(0.5)  # brief delay between queries

    logger.info("NewsAPI total: %d articles from %d queries", len(all_articles), len(queries))
    return all_articles
