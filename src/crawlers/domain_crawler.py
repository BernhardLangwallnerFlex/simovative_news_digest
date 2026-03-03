"""HTML domain crawler for university news pages.

Uses two-tier article discovery:
1. Embedding-based link classifier (sentence-transformers) with heuristic bonuses
2. OpenAI LLM fallback when classifier finds zero articles
3. Readability-based content extraction for clean article text
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from openai import OpenAI
from playwright.sync_api import sync_playwright

from src.processing.normalizer import _parse_date
from src.utils.scraping_helpers import dismiss_cookies
from dotenv import load_dotenv
load_dotenv()


try:
    from readability import Document as ReadabilityDocument
    _READABILITY_AVAILABLE = True
except ImportError:
    _READABILITY_AVAILABLE = False

logger = logging.getLogger(__name__)
logging.getLogger("readability.readability").setLevel(logging.WARNING)

# Common German date patterns (used by article page date extraction)
_DATE_PATTERNS = [
    r"\d{4}-\d{2}-\d{2}",
    r"\d{2}\.\d{2}\.\d{4}",
    (
        r"\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni"
        r"|Juli|August|September|Oktober|November|Dezember)"
        r"\s*\d{4}"
    ),
]

# --- Configuration ---
_READABILITY_MIN_CHARS = 200
_LLM_FALLBACK_ENABLED = os.getenv("DOMAIN_CRAWLER_LLM_FALLBACK", "true").lower() == "true"
_LLM_MAX_HTML_CHARS = 8000


def _extract_date_from_article_html(article_soup: BeautifulSoup) -> str | None:
    """Extract publication date from article page HTML using multiple strategies.

    Tries sources in order of reliability. Returns the first date string found,
    or None if no date can be extracted.
    """
    # Strategy 1: article:published_time meta tag (Open Graph, most reliable)
    meta = article_soup.find("meta", property="article:published_time")
    if meta and meta.get("content"):
        return meta["content"]

    # Strategy 2: JSON-LD structured data (datePublished)
    for script in article_soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, list):
                data = next((d for d in data if isinstance(d, dict)), {})
            if isinstance(data, dict):
                graph = data.get("@graph", [])
                if graph and isinstance(graph, list):
                    for node in graph:
                        if isinstance(node, dict):
                            date_val = node.get("datePublished") or node.get("dateCreated")
                            if date_val and isinstance(date_val, str):
                                return date_val
                date_val = data.get("datePublished") or data.get("dateCreated")
                if date_val and isinstance(date_val, str):
                    return date_val
        except (json.JSONDecodeError, AttributeError):
            continue

    # Strategy 3: Dublin Core and generic date meta tags
    for attr_name in ("date", "DC.date", "dc.date"):
        meta = article_soup.find("meta", attrs={"name": attr_name})
        if meta and meta.get("content"):
            return meta["content"]

    # Strategy 4: article:modified_time (fallback when published_time absent)
    meta = article_soup.find("meta", property="article:modified_time")
    if meta and meta.get("content"):
        return meta["content"]

    # Strategy 5: <time> element
    time_tag = article_soup.find("time")
    if time_tag:
        val = time_tag.get("datetime") or time_tag.get_text(strip=True)
        if val:
            return val

    # Strategy 6: Regex scan of visible body text (first 5000 chars to avoid
    # false positives from footers/copyright lines)
    body = article_soup.find("body")
    if body:
        body_text = body.get_text()[:5000]
        for pattern in _DATE_PATTERNS:
            match = re.search(pattern, body_text)
            if match:
                return match.group(0)

    return None


def _is_too_old(date_str: str | None, days_back: int) -> bool:
    """Return True if date is older than days_back days. False if unknown."""
    if not date_str or days_back <= 0:
        return False
    cutoff = datetime.utcnow().date() - timedelta(days=days_back)

    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", date_str)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date() < cutoff
        except ValueError:
            return False

    m = re.match(r"(\d{1,2})\.(\d{2})\.(\d{4})", date_str)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date() < cutoff
        except ValueError:
            return False

    return False


def _extract_article_text(html: str) -> str:
    """Extract main article content using Readability, falling back to get_text()."""
    if _READABILITY_AVAILABLE:
        try:
            doc = ReadabilityDocument(html)
            summary_html = doc.summary()
            summary_soup = BeautifulSoup(summary_html, "lxml")
            text = summary_soup.get_text(separator="\n", strip=True)
            if len(text) >= _READABILITY_MIN_CHARS:
                return text[:15000]
            logger.debug("Readability returned only %d chars, falling back", len(text))
        except Exception as e:
            logger.debug("Readability extraction failed: %s", e)

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return soup.get_text(separator="\n", strip=True)[:15000]


def _extract_link_snippets(soup: BeautifulSoup, base_domain: str, max_chars: int) -> str:
    """Build compact 'URL | text | context' representation of same-domain links."""
    lines = []
    seen = set()
    own_netloc = urlparse(base_domain).netloc

    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        full_url = urljoin(base_domain, href)
        if urlparse(full_url).netloc != own_netloc:
            continue
        if full_url in seen:
            continue
        seen.add(full_url)

        link_text = a.get_text(strip=True)
        parent = a.find_parent(["li", "div", "article", "section", "td"])
        context = parent.get_text(" ", strip=True)[:120] if parent else link_text

        lines.append(f"{full_url} | {link_text[:80]} | {context[:120]}")

    return "\n".join(lines)[:max_chars]


def _llm_discover_links(link_snippets: str, domain_url: str) -> list[dict]:
    """Use LLM to identify article links when heuristics found too few."""
    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        model = os.getenv("OPENAI_MODEL_NAME", "gpt-4.1")

        system_prompt = (
            "You are a web scraping assistant. Given a list of links from a university "
            "or organization website, identify which URLs are individual news articles, "
            "blog posts, press releases, or announcements. Exclude navigation links, "
            "category pages, pagination, search pages, and contact/about pages.\n"
            "Return a JSON object with an \"articles\" key containing an array of objects "
            "with \"url\" and \"title\" keys. Use the link text as the title. "
            "Maximum 20 items. Return {\"articles\": []} if no articles found."
        )

        user_prompt = (
            f"Website: {domain_url}\n\n"
            f"Links (format: URL | link text | surrounding context):\n"
            f"{link_snippets}\n\n"
            "Identify which are individual article/news/press release pages."
        )

        response = client.chat.completions.create(
            model=model,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
            max_tokens=1000,
        )

        data = json.loads(response.choices[0].message.content)

        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = next((v for v in data.values() if isinstance(v, list)), [])
        else:
            items = []

        result = []
        for item in items:
            if isinstance(item, dict) and item.get("url") and item.get("title"):
                result.append({"url": item["url"], "title": item["title"], "date": None})

        logger.info("LLM fallback discovered %d article links on %s", len(result), domain_url)
        return result

    except Exception as e:
        logger.warning("LLM link discovery failed for %s: %s", domain_url, e)
        return []


def crawl_domain(domain_url: str, max_articles: int = 20, days_back: int = 0) -> list[dict]:
    """Crawl a domain to discover and scrape article content.

    Opens a single browser instance: first loads the listing page to discover
    article URLs (heuristics + optional LLM fallback), then visits each article
    to extract content via Readability.

    Args:
        domain_url: Base URL of the domain news section.
        max_articles: Maximum number of articles to return.
        days_back: Skip articles older than this many days (0 = no filter).

    Returns:
        List of raw article dicts with full text content.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # --- Phase 1: Discover article URLs from listing page ---
            try:
                page.goto(domain_url, timeout=30000, wait_until="networkidle")
            except Exception:
                logger.info("networkidle timeout for %s, retrying with domcontentloaded", domain_url)
                page.goto(domain_url, timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)

            dismiss_cookies(page)
            html = page.content()

            soup = BeautifulSoup(html, "lxml")
            parsed_base = urlparse(domain_url)
            base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

            # --- Primary: Embedding-based link classifier ---
            from src.crawlers.link_classifier import LinkClassifier
            from config import LINK_DISCOVERY_THRESHOLD
            classifier = LinkClassifier.get_instance()
            discovered = classifier.classify_page_links(
                soup, domain_url, max_articles=max_articles,
                threshold=LINK_DISCOVERY_THRESHOLD,
            )

            # --- Fallback: OpenAI LLM when classifier found nothing ---
            if not discovered and _LLM_FALLBACK_ENABLED:
                logger.info(
                    "Link classifier found 0 articles on %s — triggering LLM fallback",
                    domain_url,
                )
                link_snippets = _extract_link_snippets(soup, base_domain, _LLM_MAX_HTML_CHARS)
                if link_snippets:
                    discovered = _llm_discover_links(link_snippets, domain_url)

            # Date filter: discard items that are provably too old
            if days_back > 0:
                before = len(discovered)
                discovered = [d for d in discovered if not _is_too_old(d["date"], days_back)]
                if len(discovered) < before:
                    logger.info("Date filter removed %d old items on %s",
                                before - len(discovered), domain_url)

            if not discovered:
                logger.info("No article links found on %s", domain_url)
                browser.close()
                return []

            logger.info("Discovered %d article URLs on %s", len(discovered), domain_url)

            # --- Phase 2: Scrape each article for full text ---
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

                    # Extract date (Phase 1 date takes precedence if present)
                    pub_date = item["date"] or _extract_date_from_article_html(article_soup)

                    # Phase 2 date filter: skip articles that are provably too old
                    if days_back > 0 and _is_too_old(_parse_date(pub_date), days_back):
                        logger.debug(
                            "Phase 2 date filter: skipping %s (date=%s)", article_url, pub_date
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
                        "published_at": item["date"],
                        "raw_text": "",
                        "source_type": "university_news",
                        "source_name": parsed_base.netloc,
                        "source_domain": domain_url,
                        "crawl_error": str(e),
                    })

            browser.close()
            logger.info("Domain %s: scraped %d articles", domain_url, len(articles))
            return articles

    except Exception as e:
        logger.error("Domain crawl failed for %s: %s", domain_url, e)
        return []
