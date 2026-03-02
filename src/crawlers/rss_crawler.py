"""RSS/Atom feed crawler."""

import logging
from datetime import datetime
from time import mktime

import feedparser

logger = logging.getLogger(__name__)


def parse_rss_feeds(
    feed_urls: list[str],
    start_date: str,
    end_date: str,
    max_per_feed: int = 20,
) -> list[dict]:
    """Parse multiple RSS/Atom feeds and return articles within a date range.

    Args:
        feed_urls: List of RSS/Atom feed URLs to parse.
        start_date: Start of time window (YYYY-MM-DD).
        end_date: End of time window (YYYY-MM-DD).
        max_per_feed: Maximum articles per feed.

    Returns:
        List of raw article dicts.
    """
    dt_start = datetime.strptime(start_date, "%Y-%m-%d")
    dt_end = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)

    all_articles: list[dict] = []

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.warning("Failed to parse RSS feed %s: %s", feed_url, e)
            continue

        feed_title = feed.feed.get("title", feed_url)
        articles_from_feed: list[dict] = []

        for entry in feed.entries:
            pub_date = None
            pub_date_str = None
            for date_field in ("published_parsed", "updated_parsed"):
                parsed = entry.get(date_field)
                if parsed:
                    try:
                        pub_date = datetime.fromtimestamp(mktime(parsed))
                        pub_date_str = pub_date.strftime("%Y-%m-%d")
                    except Exception:
                        pass
                    break

            if pub_date and (pub_date < dt_start or pub_date > dt_end):
                continue

            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue

            summary = entry.get("summary", "").strip()

            articles_from_feed.append({
                "title": title,
                "url": link,
                "published_at": pub_date_str,
                "raw_text": summary,
                "source_type": "rss",
                "source_name": feed_title,
                "source_feed": feed_url,
                "author": entry.get("author"),
                "crawl_error": None,
            })

        articles_from_feed = articles_from_feed[:max_per_feed]
        all_articles.extend(articles_from_feed)
        logger.info("RSS feed %s (%s): %d articles", feed_title, feed_url, len(articles_from_feed))

    logger.info("RSS total: %d articles from %d feeds", len(all_articles), len(feed_urls))
    return all_articles
