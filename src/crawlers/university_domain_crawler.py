"""Pattern-based university news crawler.

Uses URL patterns from news_article_patterns.json to identify article links
on university listing pages, then scrapes each article for content.
"""

import json
import logging
import math
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright

from config import CRAWLER_WORKERS, LINK_CLASSIFIER_ENABLED, LINK_DISCOVERY_THRESHOLD
from src.crawlers.domain_crawler import (
    _extract_article_text,
    _extract_date_from_article_html,
    _is_too_old,
)
from src.processing.normalizer import _parse_date
from src.utils.scraping_helpers import dismiss_cookies

_WARN_PATTERN_MATCHES = 50   # Log warning when patterns match this many
_HARD_PATTERN_LIMIT = 500    # Fall back to classifier only at this extreme

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_PATTERNS_FILES = [
    _PROJECT_ROOT / "news_article_patterns.json",
    _PROJECT_ROOT / "org_news_article_patterns.json",
]

# Placeholder → regex mapping
_PLACEHOLDER_REGEX = {
    "{slug}": r"[a-zA-Z0-9](?:[a-zA-Z0-9._~%-]*[a-zA-Z0-9])?",
    "{Slug}": r"[a-zA-Z0-9](?:[a-zA-Z0-9._~%-]*[a-zA-Z0-9])?",  # deprecated alias
    # long_slug requires at least 3 hyphen-separated tokens — for root-level
    # article URLs where a plain {slug} would match every nav link.
    "{long_slug}": r"[a-zA-Z0-9]+(?:-[a-zA-Z0-9]+){2,}",
    "{YYYY}": r"\d{4}",
    "{MM}": r"\d{1,2}",
    "{DD}": r"\d{1,2}",
    "{id}": r"\d+",
    "{YYYY-sem}": r"\d{4}-\d+",
    "{monat}": r"[a-zäöü]+",
}


def _load_patterns() -> dict[str, dict]:
    """Load all pattern JSON files and compile URL patterns to regexes.

    Returns:
        Dict mapping normalized listing page URL → {"patterns": list[re.Pattern], "excludes": list[re.Pattern]}.
    """
    result: dict[str, dict] = {}

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

            # Collect exclude patterns
            exclude_compiled = []
            for template in info.get("exclude_url_patterns", []):
                regex = _template_to_regex(template)
                if regex:
                    exclude_compiled.append(re.compile(regex))

            if compiled:
                # Normalize: store with and without trailing slash
                normalized = listing_url.rstrip("/")
                result[normalized] = {
                    "patterns": compiled,
                    "excludes": exclude_compiled,
                    "wait_for_selector": info.get("wait_for_selector"),
                }

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
_cached_patterns: dict[str, dict] | None = None


def _get_patterns() -> dict[str, dict]:
    """Get cached patterns, loading from file on first call."""
    global _cached_patterns
    if _cached_patterns is None:
        _cached_patterns = _load_patterns()
    return _cached_patterns


def _find_matching_links(
    soup: BeautifulSoup,
    base_url: str,
    patterns: list[re.Pattern],
    excludes: list[re.Pattern] | None = None,
) -> list[dict]:
    """Find all links on the page that match any of the article URL patterns.

    Returns:
        List of {"url": str, "title": str} dicts, deduplicated by URL.
    """
    seen: set[str] = set()
    matches: list[dict] = []
    excludes = excludes or []

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue

        full_url = urljoin(base_url, href)
        # Also try without query string/fragment (some sites add cHash etc.)
        url_no_query = full_url.split("?")[0].split("#")[0]
        # Normalize: remove trailing slash for matching
        full_stripped = full_url.rstrip("/")
        no_query_stripped = url_no_query.rstrip("/")

        if no_query_stripped in seen:
            continue

        # Check exclude patterns first
        excluded = False
        for ex in excludes:
            if ex.match(full_url) or ex.match(no_query_stripped):
                excluded = True
                break
        if excluded:
            continue

        # Try all variants: with query, without query, with/without trailing slash
        candidates = {full_url, full_stripped, url_no_query, no_query_stripped, no_query_stripped + "/"}
        for pattern in patterns:
            if any(pattern.match(c) for c in candidates):
                seen.add(no_query_stripped)
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
    # Normalize lookup: strip trailing slash to match stored keys
    entry = patterns_map.get(domain_url.rstrip("/"))

    if not entry:
        logger.warning(
            "No URL pattern found for %s — skipping", domain_url
        )
        return []

    patterns = entry["patterns"]
    excludes = entry.get("excludes", [])
    wait_for_selector = entry.get("wait_for_selector")

    own_browser = browser is None
    pw = None
    page = None
    try:
        if own_browser:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)

        page = browser.new_page()
        parsed_base = urlparse(domain_url)

        # --- Fetch listing page ---
        try:
            page.goto(domain_url, timeout=30000, wait_until="networkidle")
        except PlaywrightTimeoutError:
            logger.info("networkidle timeout for %s, retrying with domcontentloaded", domain_url)
            page.goto(domain_url, timeout=30000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

        dismiss_cookies(page)

        # Per-domain hydration wait for JS-rendered listings (e.g. iu.de)
        if wait_for_selector:
            try:
                page.wait_for_selector(wait_for_selector, timeout=10000)
            except PlaywrightTimeoutError:
                logger.info(
                    "wait_for_selector %r timeout on %s — continuing",
                    wait_for_selector, domain_url,
                )

        # --- Find article links via pattern matching (with scroll-retry for JS sites) ---
        _MAX_RENDER_RETRIES = 2
        discovered = []
        for _attempt in range(1 + _MAX_RENDER_RETRIES):
            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            discovered = _find_matching_links(soup, domain_url, patterns, excludes)
            if discovered or _attempt == _MAX_RENDER_RETRIES:
                break
            logger.info(
                "No pattern matches on %s (attempt %d), scrolling and waiting...",
                domain_url, _attempt + 1,
            )
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(3000)

        discovery_method = "pattern"

        # Safety valve: warn on broad patterns, only fall back at extreme counts
        if len(discovered) > _HARD_PATTERN_LIMIT:
            logger.warning(
                "Pattern critically broad: %d matches on %s — falling back to classifier",
                len(discovered), domain_url,
            )
            discovered = []
        elif len(discovered) > _WARN_PATTERN_MATCHES:
            logger.warning(
                "Pattern broad: %d matches on %s — using first %d",
                len(discovered), domain_url, max_articles,
            )

        # Fallback: use LinkClassifier when pattern matching yields nothing
        if not discovered and LINK_CLASSIFIER_ENABLED:
            from src.crawlers.link_classifier import LinkClassifier
            classifier = LinkClassifier.get_instance()
            fallback_results = classifier.classify_page_links(
                soup, domain_url,
                max_articles=max_articles,
                threshold=LINK_DISCOVERY_THRESHOLD,
            )
            if fallback_results:
                discovered = [{"url": r["url"], "title": r["title"]} for r in fallback_results]
                discovery_method = "classifier_fallback"
                logger.info(
                    "LinkClassifier fallback found %d articles on %s",
                    len(discovered), domain_url,
                )

        if not discovered:
            logger.info("No article links found on %s (patterns + classifier)", domain_url)
            return []

        logger.info(
            "%s discovery found %d article URLs on %s",
            discovery_method, len(discovered), domain_url,
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
                except PlaywrightTimeoutError:
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
                    "discovery_method": discovery_method,
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
                    "discovery_method": discovery_method,
                    "crawl_error": str(e),
                })

        logger.info("University domain %s: scraped %d articles", domain_url, len(articles))
        return articles

    except Exception as e:
        logger.error("University domain crawl failed for %s: %s", domain_url, e)
        return []
    finally:
        if page and not page.is_closed():
            page.close()
        if own_browser:
            browser.close()
            pw.stop()


def _crawl_domain_chunk(
    domain_urls: list[str],
    max_articles: int,
    days_back: int,
) -> tuple[list[dict], list[dict]]:
    """Crawl a chunk of domains in a single process with its own browser.

    Designed to run inside a ProcessPoolExecutor worker. Each worker gets
    its own Playwright browser instance for full isolation.

    Returns:
        (articles, domain_stats) tuple.
    """
    articles: list[dict] = []
    stats: list[dict] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        for domain_url in domain_urls:
            if not browser.is_connected():
                logger.warning("Browser disconnected — relaunching before %s", domain_url)
                try:
                    browser.close()
                except Exception:
                    pass
                browser = pw.chromium.launch(headless=True)

            try:
                result = crawl_university_domain(
                    domain_url,
                    max_articles=max_articles,
                    days_back=days_back,
                    browser=browser,
                )
                articles.extend(result)
                method = result[0]["discovery_method"] if result else "none"
                stats.append({
                    "domain": domain_url,
                    "articles": len(result),
                    "method": method,
                })
            except Exception as e:
                logger.error("University domain crawl failed for %s: %s", domain_url, e)
                stats.append({
                    "domain": domain_url,
                    "articles": 0,
                    "method": "error",
                })

        try:
            browser.close()
        except Exception:
            pass

    return articles, stats


def crawl_all_university_domains(
    university_urls: list[str],
    max_articles: int = 20,
    days_back: int = 0,
) -> list[dict]:
    """Crawl all university news pages using pattern-based link discovery.

    Splits domains across CRAWLER_WORKERS parallel processes, each with its
    own Playwright browser instance.

    Args:
        university_urls: List of listing page URLs.
        max_articles: Maximum articles per domain.
        days_back: Skip articles older than this many days.

    Returns:
        Combined list of raw article dicts from all domains.
    """
    if not university_urls:
        return []

    workers = min(CRAWLER_WORKERS, len(university_urls))

    # Split URLs into roughly equal chunks
    chunk_size = math.ceil(len(university_urls) / workers)
    chunks = [
        university_urls[i : i + chunk_size]
        for i in range(0, len(university_urls), chunk_size)
    ]

    logger.info(
        "Crawling %d domains with %d workers (%d domains/worker)",
        len(university_urls), len(chunks), chunk_size,
    )

    all_articles: list[dict] = []
    domain_stats: list[dict] = []

    with ProcessPoolExecutor(max_workers=len(chunks)) as executor:
        futures = {
            executor.submit(_crawl_domain_chunk, chunk, max_articles, days_back): i
            for i, chunk in enumerate(chunks)
        }
        for future in as_completed(futures):
            try:
                articles, stats = future.result()
                all_articles.extend(articles)
                domain_stats.extend(stats)
            except Exception as e:
                chunk_idx = futures[future]
                logger.error("Domain crawl worker %d failed: %s", chunk_idx, e)

    # --- Per-domain summary ---
    working = sum(1 for s in domain_stats if s["articles"] > 0)
    logger.info(
        "University domain crawling complete: %d articles from %d/%d domains",
        len(all_articles), working, len(university_urls),
    )
    for s in domain_stats:
        if s["articles"] == 0:
            logger.warning(
                "ZERO articles: %s (method=%s)", s["domain"], s["method"],
            )

    return all_articles
