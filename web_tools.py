"""Web search tool using SerpAPI (Google Search)."""

import logging

from crewai.tools import tool
from serpapi import GoogleSearch

from src.shared.config import SERPAPI_KEY

logger = logging.getLogger(__name__)


def _serpapi_search(query: str, num: int = 10) -> list[dict]:
    """Run a Google search via SerpAPI and return organic results.

    Returns list of dicts with keys: title, link, snippet.
    """
    if not SERPAPI_KEY:
        logger.error("SERPAPI_KEY not set — cannot perform web search")
        return []
    try:
        params = {
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": num,
            "gl": "de",   # country: Germany
            "hl": "de",   # language: German
            "engine": "google",
        }
        search = GoogleSearch(params)
        results = search.get_dict()
        return results.get("organic_results", [])
    except Exception as e:
        logger.warning("SerpAPI search failed for '%s': %s", query, e)
        return []


@tool("web_search")
def web_search(query: str) -> str:
    """Search the web using Google (via SerpAPI) and return relevant results.

    Args:
        query: The search query string.

    Returns:
        Formatted search results with title, URL, and snippet.
    """
    results = _serpapi_search(query)

    if not results:
        return "No results found."

    lines = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        url = r.get("link", "")
        snippet = r.get("snippet", "")
        lines.append(f"{i}. {title}\n   URL: {url}\n   {snippet}")
    return "\n\n".join(lines)
