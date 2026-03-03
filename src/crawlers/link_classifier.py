"""Sentence-embedding based classifier for article link discovery.

Uses a small multilingual model to score links by similarity to
'news article' vs 'navigation link' reference descriptions. This replaces
fragile CSS-class / URL-pattern heuristics with a model that generalises
across diverse CMS structures.
"""

import logging
import re
from urllib.parse import urlparse, urljoin

import numpy as np
from bs4 import BeautifulSoup

from src.processing.normalizer import _parse_date

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Reference descriptions (bilingual) — pre-encoded once at model load
# ---------------------------------------------------------------------------

ARTICLE_REFERENCES = [
    "news article about a university",
    "press release from a higher education institution",
    "blog post about academic events or campus news",
    "announcement from a university or research organisation",
    "Pressemitteilung einer Hochschule",
    "Nachricht über eine Universität",
    "Meldung zu Hochschulthemen",
    "Neuigkeit aus der Hochschule",
    "Aktuelles von der Universität",
]

NON_ARTICLE_REFERENCES = [
    "website navigation link",
    "contact page",
    "about us page",
    "search page or search form",
    "category listing or archive overview",
    "login or registration page",
    "Impressum Datenschutz",
    "Startseite Navigation Kontakt",
    "Sitemap or footer link",
    "study program overview or course catalog",
    "application and enrollment information",
    "Studienangebot Bewerbung Studiengang",
    "library services or campus facilities",
    "staff directory or department page",
]

# ---------------------------------------------------------------------------
# Fast pre-filters (applied before model inference)
# ---------------------------------------------------------------------------

EXCLUDE_PATHS = {
    "/impressum", "/datenschutz", "/kontakt", "/contact",
    "/login", "/search", "/suche", "/sitemap", "/agb",
    "/privacy", "/terms", "/cookie", "/disclaimer",
}

EXCLUDE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg",
                      ".css", ".js", ".xml", ".rss", ".zip", ".mp4", ".mp3"}

# Pagination patterns to exclude
_PAGINATION_RE = re.compile(r"/(seite|page)[-/]\d+|[?&]page=\d+", re.IGNORECASE)

# Heuristic keyword patterns (used as scoring bonus, not hard filter)
_NEWS_CSS_RE = re.compile(
    r"(?i)(news|article|post|blog|entry|item|teaser|beitrag|meldung|aktuell)"
)
# Matches article detail URLs: a news-like path segment followed by a slug.
# e.g. /news-detail/pixel-campus... or /press-releases/details/article-slug
# Does NOT match listing pages like /news-and-events/ or /news/
_ARTICLE_URL_RE = re.compile(
    r"/(?:news-detail|newsdetail|press-releases?|pressemitteilungen?"
    r"|aktuelles|nachrichten|meldungen?|beitrag|beitraege|artikel"
    r"|news|blog|presse|mitteilungen?|neuigkeiten?"
    r"|publikationen?|announcements?|pm)"
    r"/[^/?#]+",
    re.IGNORECASE,
)


class LinkClassifier:
    """Singleton classifier that scores links as article vs. navigation."""

    _instance = None

    def __init__(self, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        from sentence_transformers import SentenceTransformer

        logger.info("Loading link classifier model: %s", model_name)
        self.model = SentenceTransformer(model_name)
        self._article_emb = self.model.encode(ARTICLE_REFERENCES, normalize_embeddings=True)
        self._nav_emb = self.model.encode(NON_ARTICLE_REFERENCES, normalize_embeddings=True)
        logger.info("Link classifier ready (%d article refs, %d nav refs)",
                     len(ARTICLE_REFERENCES), len(NON_ARTICLE_REFERENCES))

    @classmethod
    def get_instance(cls, model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"):
        if cls._instance is None:
            cls._instance = cls(model_name)
        return cls._instance

    def score_links(self, link_texts: list[str]) -> list[float]:
        """Return contrastive scores for a batch of link descriptions.

        Score = max(similarity to article refs) - max(similarity to nav refs).
        Positive scores indicate article-like links.
        """
        if not link_texts:
            return []
        embeddings = self.model.encode(link_texts, batch_size=64, normalize_embeddings=True)
        article_sims = (embeddings @ self._article_emb.T).max(axis=1)
        nav_sims = (embeddings @ self._nav_emb.T).max(axis=1)
        return (article_sims - nav_sims).tolist()

    def classify_page_links(
        self,
        soup: BeautifulSoup,
        base_domain: str,
        max_articles: int = 20,
        threshold: float = 0.15,
        days_back: int = 0,
    ) -> list[dict]:
        """Extract, score, and rank article links from a parsed listing page.

        Returns list of {"url", "title", "date", "score"} dicts, sorted by
        score descending, capped at max_articles.
        """
        parsed_base = urlparse(base_domain)
        own_netloc = parsed_base.netloc

        # --- Collect all same-domain links with context ---
        # Use dict keyed by URL to prefer the version with the longest text
        # (same URL often appears as image link, title link, and "Weiterlesen")
        url_best: dict[str, dict] = {}

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            full_url = urljoin(base_domain, href)
            parsed = urlparse(full_url)

            # Same-domain only
            if parsed.netloc != own_netloc:
                continue

            # Fast pre-filter: excluded paths and extensions
            path_lower = parsed.path.rstrip("/").lower()
            if path_lower in EXCLUDE_PATHS:
                continue
            if any(path_lower.endswith(ext) for ext in EXCLUDE_EXTENSIONS):
                continue
            # Skip the listing page itself
            if full_url.rstrip("/") == base_domain.rstrip("/"):
                continue
            # Skip pagination links
            if _PAGINATION_RE.search(full_url):
                continue

            link_text = a_tag.get_text(strip=True)
            parent = a_tag.find_parent(["li", "div", "article", "section", "td"])
            context = parent.get_text(" ", strip=True)[:120] if parent else link_text

            # Heuristic bonus signals
            in_article_tag = a_tag.find_parent("article") is not None
            css_match = False
            if parent:
                classes = " ".join(parent.get("class", []))
                css_match = bool(_NEWS_CSS_RE.search(classes))
            url_match = bool(_ARTICLE_URL_RE.search(parsed.path))

            entry = {
                "url": full_url,
                "link_text": link_text,
                "context": context,
                "path": parsed.path,
                "in_article_tag": in_article_tag,
                "css_match": css_match,
                "url_match": url_match,
                "date": self._extract_nearby_date(parent) if parent else None,
            }

            # Keep the version with the longest link text per URL
            existing = url_best.get(full_url)
            if existing is None or len(link_text) > len(existing["link_text"]):
                url_best[full_url] = entry

        candidates = list(url_best.values())

        if not candidates:
            logger.info("No candidate links found on %s", base_domain)
            return []

        # --- Build text representations and score ---
        link_texts = [
            f"{c['link_text'][:80]} | {c['path']} | {c['context'][:120]}"
            for c in candidates
        ]
        model_scores = self.score_links(link_texts)

        # --- Combine model score with heuristic bonuses ---
        results = []
        for candidate, model_score in zip(candidates, model_scores):
            bonus = 0.0
            if candidate["in_article_tag"]:
                bonus += 0.15
            if candidate["css_match"]:
                bonus += 0.10
            if candidate["url_match"]:
                bonus += 0.20

            final_score = model_score + bonus
            if final_score < threshold:
                continue

            title = candidate["link_text"]
            if not title or len(title) < 5:
                continue

            results.append({
                "url": candidate["url"],
                "title": title,
                "date": candidate["date"],
                "score": round(final_score, 4),
            })

        # Sort by score descending, cap at max_articles
        results.sort(key=lambda x: x["score"], reverse=True)
        results = results[:max_articles]

        logger.info(
            "Link classifier: %d/%d candidates accepted on %s (threshold=%.2f)",
            len(results), len(candidates), base_domain, threshold,
        )
        if results:
            logger.debug(
                "Top scores: %s",
                [(r["score"], r["url"][:60]) for r in results[:5]],
            )

        return results

    @staticmethod
    def _extract_nearby_date(element) -> str | None:
        """Extract date from nearby text using common German/ISO patterns."""
        if not element:
            return None
        text = element.get_text()
        date_patterns = [
            r"\d{4}-\d{2}-\d{2}",
            r"\d{2}\.\d{2}\.\d{4}",
            (
                r"\d{1,2}\.\s*(?:Januar|Februar|März|April|Mai|Juni"
                r"|Juli|August|September|Oktober|November|Dezember)"
                r"\s*\d{4}"
            ),
        ]
        for pattern in date_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(0)
        return None
