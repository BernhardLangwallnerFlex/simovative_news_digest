"""Article normalization to canonical schema."""

import hashlib
import logging
import re
from datetime import datetime
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

GERMAN_MONTHS = {
    "Januar": "01", "Februar": "02", "März": "03", "April": "04",
    "Mai": "05", "Juni": "06", "Juli": "07", "August": "08",
    "September": "09", "Oktober": "10", "November": "11", "Dezember": "12",
}


def _normalize_url(url: str) -> str:
    """Lowercase scheme/host, strip trailing slash and tracking params."""
    parsed = urlparse(url)
    clean = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}{parsed.path}"
    return clean.rstrip("/")


def _parse_date(date_str: str | None) -> str | None:
    """Parse mixed German/ISO date strings to YYYY-MM-DD."""
    if not date_str:
        return None
    date_str = date_str.strip()

    # Already ISO 8601 (possibly with time component)
    m = re.match(r"(\d{4}-\d{2}-\d{2})", date_str)
    if m:
        return m.group(1)

    # DD.MM.YYYY
    m = re.match(r"(\d{1,2})\.(\d{2})\.(\d{4})", date_str)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1).zfill(2)}"

    # DD. MonthName YYYY
    for month_name, month_num in GERMAN_MONTHS.items():
        m = re.match(rf"(\d{{1,2}})\.\s*{month_name}\s*(\d{{4}})", date_str)
        if m:
            return f"{m.group(2)}-{month_num}-{m.group(1).zfill(2)}"

    return None


def _infer_region(url: str) -> str:
    host = urlparse(url).netloc.lower()
    if host.endswith(".at"):
        return "AT"
    if host.endswith(".ch"):
        return "CH"
    if host.endswith(".eu"):
        return "EU"
    # Default for .de and everything else in this German-focused system
    return "DE"


def _make_article_id(normalized_url: str, published_at: str | None) -> str:
    raw = (normalized_url + (published_at or "")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _make_content_hash(raw_text: str) -> str:
    return hashlib.sha256((raw_text or "").encode("utf-8")).hexdigest()


def normalize_article(raw: dict) -> dict:
    """Transform a raw crawled dict into the canonical article schema."""
    normalized_url = _normalize_url(raw.get("url", ""))
    published_at = _parse_date(raw.get("published_at"))
    raw_text = raw.get("raw_text", "") or ""
    crawl_ts = datetime.utcnow().isoformat() + "Z"

    return {
        "schema_version": "1.0",
        "article_id": _make_article_id(normalized_url, published_at),
        "source": {
            "type": raw.get("source_type", "rss"),
            "name": raw.get("source_name") or raw.get("source_feed") or raw.get("source_domain", ""),
            "url": normalized_url,
            "region": _infer_region(normalized_url),
        },
        "content": {
            "title": raw.get("title", "").strip(),
            "raw_text": raw_text,
            "published_at": published_at,
            "author": raw.get("author"),
            "language": "de",
        },
        "crawl_metadata": {
            "crawl_timestamp": crawl_ts,
            "content_hash": _make_content_hash(raw_text),
            "deduplicated": False,
        },
        "analysis": {
            "processed": False,
            "taxonomy_version": "1.0",
            "primary_category": None,
            "secondary_tags": [],
            "relevance_score": None,
            "priority_score": None,
            "confidence_score": None,
            "entities": {
                "universities": [],
                "persons": [],
                "roles": [],
                "vendors": [],
                "technologies": [],
                "regions": [],
            },
            "signal_summary": None,
            "sales_relevance": None,
        },
        "digest": {
            "included": False,
            "digest_id": None,
            "priority_bucket": None,
        },
    }


def normalize_articles(raw_articles: list[dict]) -> list[dict]:
    """Normalize a list of raw crawled articles."""
    normalized = []
    for raw in raw_articles:
        try:
            normalized.append(normalize_article(raw))
        except Exception as e:
            logger.warning("Failed to normalize article %s: %s", raw.get("url", "?"), e)
    return normalized
