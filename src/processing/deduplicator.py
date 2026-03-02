"""Article deduplication by ID and content hash."""

import logging

logger = logging.getLogger(__name__)

# Articles with very short raw_text all hash the same — skip content-hash
# dedup for those to avoid incorrectly dropping distinct articles.
_MIN_TEXT_LENGTH = 50


def deduplicate(articles: list[dict]) -> list[dict]:
    """Remove duplicate articles by article_id and content_hash.

    Pass 1: Deduplicate by article_id (same URL + same date).
    Pass 2: Deduplicate by content_hash (same text from different URLs),
             only for articles with raw_text longer than _MIN_TEXT_LENGTH.

    Returns:
        List of unique articles.
    """
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    unique: list[dict] = []
    dupes = 0

    for article in articles:
        aid = article["article_id"]
        if aid in seen_ids:
            dupes += 1
            continue
        seen_ids.add(aid)

        raw_text = article.get("content", {}).get("raw_text", "")
        chash = article["crawl_metadata"]["content_hash"]
        if len(raw_text) >= _MIN_TEXT_LENGTH and chash in seen_hashes:
            dupes += 1
            continue
        if len(raw_text) >= _MIN_TEXT_LENGTH:
            seen_hashes.add(chash)

        unique.append(article)

    logger.info("Deduplication: %d → %d articles (%d duplicates removed)",
                len(articles), len(unique), dupes)
    return unique
