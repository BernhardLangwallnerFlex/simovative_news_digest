"""Market intelligence specific crawling tools."""

import logging
import re
from datetime import datetime
from time import mktime
from urllib.parse import urljoin, urlparse

import feedparser

from bs4 import BeautifulSoup
from crewai.tools import tool
from playwright.sync_api import sync_playwright

from src.shared.tools.scraping_tools import _dismiss_cookies

logger = logging.getLogger(__name__)

# Common German date patterns
_DATE_PATTERNS = [
    (r"\d{4}-\d{2}-\d{2}", None),                          # 2026-02-15 (ISO)
    (r"\d{2}\.\d{2}\.\d{4}", None),                        # 15.02.2026
    (r"\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni"
     r"|Juli|August|September|Oktober|November|Dezember)"
     r"\s*\d{4}", None),                                    # 15. Februar 2026
]


def _extract_nearby_date(element) -> str | None:
    """Extract date from nearby text using common German/ISO patterns."""
    if not element:
        return None
    text = element.get_text()
    for pattern, _ in _DATE_PATTERNS:
        match = re.search(pattern, text)
        if match:
            return match.group(0)
    return None


@tool("crawl_domain_for_articles")
def crawl_domain_for_articles(domain_url: str, max_articles: int = 20) -> str:
    """Crawl a domain's homepage or news section to discover article URLs.

    Discovers blog posts, news articles, and press releases by looking for
    common patterns (article links, date indicators, news listings).

    Args:
        domain_url: Base URL of the domain to crawl (e.g. https://www.his.de/aktuelles).
        max_articles: Maximum number of article URLs to return (default 20).

    Returns:
        Formatted list of discovered article URLs with titles and dates.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(domain_url, timeout=30000, wait_until="networkidle")
            _dismiss_cookies(page)
            html = page.content()
            browser.close()
    except Exception as e:
        return f"Error crawling {domain_url}: {e}"

    soup = BeautifulSoup(html, "lxml")
    parsed_base = urlparse(domain_url)
    base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

    article_links: list[dict] = []
    seen_urls: set[str] = set()

    def _add_article(url: str, title: str, date_str: str | None) -> None:
        if url in seen_urls or len(article_links) >= max_articles:
            return
        if not title or len(title) < 5:
            return
        seen_urls.add(url)
        article_links.append({"url": url, "title": title, "date": date_str})

    # Strategy 1: Look for <article> tags with links
    for article in soup.find_all("article"):
        link = article.find("a", href=True)
        if link:
            full_url = urljoin(base_domain, link["href"].strip())
            title = link.get_text(strip=True)
            date_str = _extract_nearby_date(article)
            _add_article(full_url, title, date_str)

    # Strategy 2: Look for common news/blog listing patterns
    for container in soup.find_all(["div", "li", "section"], class_=re.compile(
        r"(?i)(news|article|post|blog|entry|item|teaser|beitrag|meldung|aktuell)"
    )):
        link = container.find("a", href=True)
        if link:
            full_url = urljoin(base_domain, link["href"].strip())
            title = link.get_text(strip=True)
            date_str = _extract_nearby_date(container)
            _add_article(full_url, title, date_str)

    # Strategy 3: Look for links with news/blog URL path patterns
    if len(article_links) < max_articles:
        news_patterns = [
            "/news/", "/blog/", "/aktuelles/", "/presse/",
            "/meldung/", "/beitrag/", "/artikel/", "/publikation/",
        ]
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not any(p in href.lower() for p in news_patterns):
                continue
            full_url = urljoin(base_domain, href)
            title = a_tag.get_text(strip=True)
            parent = a_tag.find_parent(["div", "article", "li", "section"])
            date_str = _extract_nearby_date(parent) if parent else None
            _add_article(full_url, title, date_str)

    if not article_links:
        return f"No article links found on {domain_url}"

    lines = [f"Found {len(article_links)} articles on {domain_url}:\n"]
    for item in article_links:
        lines.append(
            f"- Title: {item['title']}\n"
            f"  URL: {item['url']}\n"
            f"  Date: {item['date'] or 'unknown'}"
        )
    return "\n\n".join(lines)


@tool("extract_article_metadata")
def extract_article_metadata(url: str) -> str:
    """Extract publication date and title from an article URL.

    Checks meta tags, <time> elements, and inline date patterns.

    Args:
        url: Article URL to extract metadata from.

    Returns:
        Structured metadata with title and publication_date.
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, timeout=30000, wait_until="networkidle")
            title = page.title()
            html = page.content()
            browser.close()
    except Exception as e:
        return f"Error extracting metadata from {url}: {e}"

    soup = BeautifulSoup(html, "lxml")

    pub_date = None

    # Strategy 1: <meta property="article:published_time">
    meta_pub = soup.find("meta", property="article:published_time")
    if meta_pub and meta_pub.get("content"):
        pub_date = meta_pub["content"]

    # Strategy 2: <time> tag with datetime attribute
    if not pub_date:
        time_tag = soup.find("time")
        if time_tag:
            pub_date = time_tag.get("datetime") or time_tag.get_text(strip=True)

    # Strategy 3: date patterns in page text
    if not pub_date:
        text = soup.get_text()
        for pattern, _ in _DATE_PATTERNS:
            match = re.search(pattern, text)
            if match:
                pub_date = match.group(0)
                break

    return f"Title: {title}\nURL: {url}\nPublication Date: {pub_date or 'unknown'}"


@tool("parse_rss_feeds")
def parse_rss_feeds(feed_urls: list[str], start_date: str, end_date: str) -> str:
    """Parse multiple RSS/Atom feeds and return articles within a date range.

    Fetches each feed URL, parses entries, and filters by publication date.
    No browser needed — pure HTTP + XML parsing.

    Args:
        feed_urls: List of RSS/Atom feed URLs to parse.
        start_date: Start of time window (YYYY-MM-DD).
        end_date: End of time window (YYYY-MM-DD).

    Returns:
        Formatted list of articles grouped by feed, with title, URL, and date.
    """
    try:
        dt_start = datetime.strptime(start_date, "%Y-%m-%d")
        dt_end = datetime.strptime(end_date, "%Y-%m-%d")
    except ValueError:
        return f"Invalid date format. Use YYYY-MM-DD. Got start={start_date}, end={end_date}"

    all_articles: list[dict] = []
    feed_summaries: list[str] = []

    for feed_url in feed_urls:
        try:
            feed = feedparser.parse(feed_url)
        except Exception as e:
            logger.warning("Failed to parse RSS feed %s: %s", feed_url, e)
            feed_summaries.append(f"Error parsing {feed_url}: {e}")
            continue

        feed_title = feed.feed.get("title", feed_url)
        articles_from_feed: list[dict] = []

        for entry in feed.entries:
            pub_date = None
            pub_date_str = "unknown"
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

            articles_from_feed.append({
                "title": title,
                "url": link,
                "date": pub_date_str,
                "domain": feed_url,
            })

        articles_from_feed = articles_from_feed[:20]
        all_articles.extend(articles_from_feed)
        feed_summaries.append(
            f"Feed: {feed_title} ({feed_url}) — {len(articles_from_feed)} articles"
        )

    if not all_articles:
        return (
            "No articles found in any RSS feeds for the given date range.\n\n"
            + "\n".join(feed_summaries)
        )

    lines = [f"Found {len(all_articles)} articles from {len(feed_urls)} RSS feeds:\n"]
    lines.append("--- Feed Summary ---")
    lines.extend(feed_summaries)
    lines.append("\n--- Articles ---")
    for item in all_articles:
        lines.append(
            f"- Title: {item['title']}\n"
            f"  URL: {item['url']}\n"
            f"  Date: {item['date']}\n"
            f"  Source Feed: {item['domain']}"
        )
    return "\n\n".join(lines)
