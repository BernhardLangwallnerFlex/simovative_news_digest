"""Playwright-based web scraping tools (generic, shared across crews)."""

import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from crewai.tools import tool
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)

# Common cookie-consent button selectors
_COOKIE_SELECTORS = [
    "button[id*='accept']",
    "button[id*='cookie']",
    "button[class*='accept']",
    "button[class*='consent']",
    "a[id*='accept']",
    "[data-testid='cookie-accept']",
    ".cookie-banner button",
    "#cookie-banner button",
]


def _dismiss_cookies(page) -> None:
    """Try to dismiss cookie banners by clicking common accept buttons."""
    for selector in _COOKIE_SELECTORS:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _scrape_url(url: str, timeout_ms: int = 30000) -> dict:
    """Scrape a single URL and return structured content."""
    result = {"url": url, "title": "", "content": "", "error": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            except Exception:
                # networkidle can fail on sites with persistent connections
                # (analytics, trackers). Fall back to domcontentloaded + wait.
                logger.info("networkidle timeout for %s, retrying with domcontentloaded", url)
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
            _dismiss_cookies(page)
            result["title"] = page.title()
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "lxml")
        # Keep nav — it often contains useful links on HEI sites
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        # Extract mailto links before converting to plain text
        mailto_links = []
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if href.lower().startswith("mailto:"):
                email = href[7:].split("?")[0]  # strip mailto: prefix and query params
                context_text = a_tag.get_text(strip=True)
                if email:
                    mailto_links.append(f"  {context_text} → {email}" if context_text else f"  {email}")

        text_content = soup.get_text(separator="\n", strip=True)

        # Append extracted emails so agents can see them
        if mailto_links:
            text_content += "\n\n--- EXTRACTED EMAIL ADDRESSES (from mailto links) ---\n"
            text_content += "\n".join(mailto_links)

        result["content"] = text_content
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Scraping failed for %s: %s", url, e)
    return result


@tool("scrape_webpage")
def scrape_webpage(url: str) -> str:
    """Scrape a single webpage and return its text content.

    Args:
        url: The full URL to scrape.

    Returns:
        The page title and main text content, or an error message.
    """
    data = _scrape_url(url)
    if data["error"]:
        return f"Error scraping {url}: {data['error']}"

    content = data["content"]
    if len(content) > 20000:
        content = content[:20000] + "\n\n[... content truncated ...]"

    return f"Page: {data['title']}\nURL: {data['url']}\n\n{content}"


@tool("scrape_multiple_pages")
def scrape_multiple_pages(urls: list[str]) -> str:
    """Scrape multiple webpages and return their combined text content.

    Args:
        urls: List of URLs to scrape (max 8 will be processed).

    Returns:
        Combined text content from all successfully scraped pages.
    """
    sections = []
    for url in urls[:8]:
        data = _scrape_url(url)
        if data["error"]:
            sections.append(f"--- {url} ---\nError: {data['error']}")
            continue
        content = data["content"]
        if len(content) > 12000:
            content = content[:12000] + "\n\n[... content truncated ...]"
        sections.append(f"--- {data['title']} ({url}) ---\n{content}")
    return "\n\n".join(sections)
