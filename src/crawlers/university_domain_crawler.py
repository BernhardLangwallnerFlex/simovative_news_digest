"""Pattern-based university news crawler.

Uses URL patterns from news_article_patterns.json to identify article links
on university listing pages, then scrapes each article for content.
"""

import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from src.crawlers.domain_crawler import (
    _extract_article_text,
    _extract_date_from_article_html,
    _is_too_old,
)
from src.processing.normalizer import _parse_date
from src.utils.scraping_helpers import dismiss_cookies

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PATTERNS_FILES = [
    _PROJECT_ROOT / "news_article_patterns.json",
    _PROJECT_ROOT / "org_news_article_patterns.json",
]

# Placeholder → regex mapping
_PLACEHOLDER_REGEX = {
    "{slug}": r"[a-z0-9][a-z0-9_-]+[a-z0-9]",
    "{Slug}": r"[A-Za-z0-9][A-Za-z0-9_-]+[A-Za-z0-9]",
    "{YYYY}": r"\d{4}",
    "{MM}": r"\d{1,2}",
    "{DD}": r"\d{1,2}",
    "{id}": r"\d+",
    "{YYYY-sem}": r"\d{4}-\d+",
    "{monat}": r"[a-z]+",
}


def _load_patterns() -> dict[str, list[re.Pattern]]:
    """Load all pattern JSON files and compile URL patterns to regexes.

    Returns:
        Dict mapping listing page URL → list of compiled regex patterns.
    """
    result: dict[str, list[re.Pattern]] = {}

    for patterns_file in _PATTERNS_FILES:
        if not patterns_file.exists():
            logger.warning("Pattern file not found: %s", patterns_file)
            continue

        with open(patterns_file, encoding="utf-8") as f:
            data = json.load(f)

        count_before = len(result)
        for listing_url, info in data.get("patterns", {}).items():
            # Collect all pattern keys (article_url_pattern, article_url_pattern_news, etc.)
            pattern_templates = []
            for key, value in info.items():
                if key.startswith("article_url_pattern") and isinstance(value, str):
                    pattern_templates.append(value)

            if not pattern_templates:
                continue

            compiled = []
            for template in pattern_templates:
                regex = _template_to_regex(template)
                if regex:
                    compiled.append(re.compile(regex))

            if compiled:
                result[listing_url] = compiled

        logger.info(
            "Loaded %d URL patterns from %s",
            len(result) - count_before,
            patterns_file.name,
        )

    logger.info("Total URL patterns loaded: %d", len(result))
    return result


def _template_to_regex(template: str) -> str | None:
    """Convert a URL template with placeholders to a regex pattern."""
    # Find all placeholders in the template
    placeholder_pattern = re.compile(r"\{[^}]+\}")
    parts = placeholder_pattern.split(template)
    placeholders = placeholder_pattern.findall(template)

    if not placeholders:
        # No placeholders — this template isn't useful for matching
        return None

    # Build regex: escape static parts, insert placeholder regexes
    regex_parts = []
    for i, static_part in enumerate(parts):
        regex_parts.append(re.escape(static_part))
        if i < len(placeholders):
            ph = placeholders[i]
            ph_regex = _PLACEHOLDER_REGEX.get(ph)
            if ph_regex is None:
                # Unknown placeholder — use a generic match
                ph_regex = r"[^/]+"
                logger.debug("Unknown placeholder %s in template %s, using generic match", ph, template)
            regex_parts.append(ph_regex)

    return "^" + "".join(regex_parts) + "$"


# Module-level cache
_cached_patterns: dict[str, list[re.Pattern]] | None = None


def _get_patterns() -> dict[str, list[re.Pattern]]:
    """Get cached patterns, loading from file on first call."""
    global _cached_patterns
    if _cached_patterns is None:
        _cached_patterns = _load_patterns()
    return _cached_patterns


def _find_matching_links(
    soup: BeautifulSoup, base_url: str, patterns: list[re.Pattern]
) -> list[dict]:
    """Find all links on the page that match any of the article URL patterns.

    Returns:
        List of {"url": str, "title": str} dicts, deduplicated by URL.
    """
    seen: set[str] = set()
    matches: list[dict] = []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        full_url = urljoin(base_url, href)
        # Normalize: remove trailing slash for matching, but keep original
        url_for_match = full_url.rstrip("/")

        if url_for_match in seen:
            continue

        for pattern in patterns:
            # Try both with and without trailing slash
            if pattern.match(full_url) or pattern.match(url_for_match):
                seen.add(url_for_match)
                title = a.get_text(strip=True) or ""
                matches.append({"url": full_url, "title": title})
                break

    return matches


def crawl_university_domain(
    domain_url: str,
    max_articles: int = 20,
    days_back: int = 0,
    *,
    browser=None,
) -> list[dict]:
    """Crawl a university news page using URL pattern matching.

    Args:
        domain_url: Listing page URL (must exist in news_article_patterns.json).
        max_articles: Maximum number of articles to return.
        days_back: Skip articles older than this many days (0 = no filter).
        browser: Optional Playwright browser instance to reuse.

    Returns:
        List of raw article dicts matching the standard crawler schema.
    """
    patterns_map = _get_patterns()
    patterns = patterns_map.get(domain_url)

    if not patterns:
        logger.warning(
            "No URL pattern found for %s — skipping", domain_url
        )
        return []

    own_browser = browser is None
    try:
        if own_browser:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)

        page = browser.new_page()
        parsed_base = urlparse(domain_url)

        # --- Fetch listing page ---
        try:
            page.goto(domain_url, timeout=30000, wait_until="networkidle")
        except Exception:
            logger.info("networkidle timeout for %s, retrying with domcontentloaded", domain_url)
            page.goto(domain_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

        dismiss_cookies(page)
        html = page.content()
        soup = BeautifulSoup(html, "lxml")

        # --- Find article links via pattern matching ---
        discovered = _find_matching_links(soup, domain_url, patterns)

        if not discovered:
            logger.info("No article links matched patterns on %s", domain_url)
            page.close()
            return []

        logger.info(
            "Pattern matching found %d article URLs on %s", len(discovered), domain_url
        )

        # Cap at max_articles
        discovered = discovered[:max_articles]

        # --- Scrape each article ---
        articles: list[dict] = []
        for item in discovered:
            article_url = item["url"]
            try:
                try:
                    page.goto(article_url, timeout=30000, wait_until="networkidle")
                except Exception:
                    page.goto(article_url, timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

                dismiss_cookies(page)
                article_html = page.content()
                article_soup = BeautifulSoup(article_html, "lxml")

                pub_date = _extract_date_from_article_html(article_soup)

                # Date filter: skip articles that are provably too old
                if days_back > 0 and _is_too_old(_parse_date(pub_date), days_back):
                    logger.debug(
                        "Date filter: skipping %s (date=%s)", article_url, pub_date
                    )
                    continue

                raw_text = _extract_article_text(article_html)
                title = page.title() or item["title"]

                articles.append({
                    "title": title,
                    "url": article_url,
                    "published_at": pub_date,
                    "raw_text": raw_text,
                    "source_type": "university_news",
                    "source_name": parsed_base.netloc,
                    "source_domain": domain_url,
                    "crawl_error": None,
                })
            except Exception as e:
                logger.warning("Failed to scrape article %s: %s", article_url, e)
                articles.append({
                    "title": item["title"],
                    "url": article_url,
                    "published_at": None,
                    "raw_text": "",
                    "source_type": "university_news",
                    "source_name": parsed_base.netloc,
                    "source_domain": domain_url,
                    "crawl_error": str(e),
                })

        page.close()
        logger.info("University domain %s: scraped %d articles", domain_url, len(articles))
        return articles

    except Exception as e:
        logger.error("University domain crawl failed for %s: %s", domain_url, e)
        return []
    finally:
        if own_browser:
            browser.close()
            pw.stop()


def crawl_all_university_domains(
    university_urls: list[str],
    max_articles: int = 20,
    days_back: int = 0,
) -> list[dict]:
    """Crawl all university news pages using pattern-based link discovery.

    Shares a single Playwright browser instance across all domains.

    Args:
        university_urls: List of listing page URLs.
        max_articles: Maximum articles per domain.
        days_back: Skip articles older than this many days.

    Returns:
        Combined list of raw article dicts from all domains.
    """
    all_articles: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        for domain_url in university_urls:
            try:
                articles = crawl_university_domain(
                    domain_url,
                    max_articles=max_articles,
                    days_back=days_back,
                    browser=browser,
                )
                all_articles.extend(articles)
            except Exception as e:
                logger.error("University domain crawl failed for %s: %s", domain_url, e)

        browser.close()

    logger.info(
        "University domain crawling complete: %d articles from %d domains",
        len(all_articles),
        len(university_urls),
    )
    return all_articles
