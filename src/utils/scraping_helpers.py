"""Playwright-based web scraping helpers."""

import logging
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
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


def dismiss_cookies(page) -> None:
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


def scrape_url(url: str, timeout_ms: int = 30000) -> dict:
    """Scrape a single URL and return structured content.

    Returns dict with keys: url, title, content, error.
    """
    result = {"url": url, "title": "", "content": "", "error": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                page.goto(url, timeout=timeout_ms, wait_until="networkidle")
            except Exception:
                logger.info("networkidle timeout for %s, retrying with domcontentloaded", url)
                page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
            dismiss_cookies(page)
            result["title"] = page.title()
            html = page.content()
            browser.close()

        soup = BeautifulSoup(html, "lxml")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()

        result["content"] = soup.get_text(separator="\n", strip=True)
    except Exception as e:
        result["error"] = str(e)
        logger.warning("Scraping failed for %s: %s", url, e)
    return result


def scrape_webpage(url: str) -> str:
    """Scrape a single webpage and return its text content."""
    data = scrape_url(url)
    if data["error"]:
        return f"Error scraping {url}: {data['error']}"

    content = data["content"]
    if len(content) > 20000:
        content = content[:20000] + "\n\n[... content truncated ...]"

    return f"Page: {data['title']}\nURL: {data['url']}\n\n{content}"
