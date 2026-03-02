"""HTML digest generation with filtering and rendering."""

import base64
import logging
from html import escape
from pathlib import Path

logger = logging.getLogger(__name__)

EXCLUDED_CATEGORIES = {"Research News", "Irrelevant"}

# Fixed display order matching the spec taxonomy
CATEGORY_ORDER = [
    "Leadership & Governance",
    "Regulatory & Policy Changes",
    "Organizational & Structural Changes",
    "Digital Strategy & IT Initiatives",
    "Funding & Investment Signals",
    "Procurement & Tenders",
    "Crisis & Risk Events",
]

_BUCKET_ORDER = {"High": 0, "Medium": 1, "Low": 2}


def _priority_bucket(priority_score: float) -> str:
    if priority_score >= 0.8:
        return "High"
    if priority_score >= 0.5:
        return "Medium"
    return "Low"


def filter_for_digest(articles: list[dict]) -> list[dict]:
    """Apply digest inclusion rules from the spec.

    Include if:
    - primary_category NOT IN {Research News, Irrelevant}
    - relevance_score >= 0.6
    - confidence_score >= 0.6
    """
    included = []
    for a in articles:
        analysis = a.get("analysis", {})
        if not analysis.get("processed"):
            continue
        if analysis.get("primary_category", "") in EXCLUDED_CATEGORIES:
            continue
        if (analysis.get("relevance_score") or 0) < 0.6:
            continue
        if (analysis.get("confidence_score") or 0) < 0.6:
            continue

        bucket = _priority_bucket(analysis.get("priority_score") or 0)
        a["digest"]["included"] = True
        a["digest"]["priority_bucket"] = bucket
        included.append(a)

    logger.info("Digest filter: %d articles included", len(included))
    return included


def _sort_articles(articles: list[dict]) -> list[dict]:
    """Sort by priority bucket (High→Medium→Low), then priority_score desc, then published_at desc."""
    def sort_key(a):
        analysis = a.get("analysis", {})
        bucket = a.get("digest", {}).get("priority_bucket", "Low")
        return (
            _BUCKET_ORDER.get(bucket, 2),
            -(analysis.get("priority_score") or 0),
            -(hash(a.get("content", {}).get("published_at") or "")),
        )
    return sorted(articles, key=sort_key)


def _load_logo_base64() -> str | None:
    """Load the Simovative logo as a base64 data URI."""
    logo_path = Path(__file__).resolve().parents[2] / "Simovative_Logo_RGB.png"
    if not logo_path.exists():
        logger.warning("Logo not found at %s", logo_path)
        return None
    data = logo_path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/png;base64,{b64}"


def generate_html_digest(articles: list[dict], run_date: str) -> str:
    """Render filtered articles as an HTML digest grouped by taxonomy category."""
    # Group articles by primary_category
    by_category: dict[str, list[dict]] = {}
    for a in articles:
        cat = a["analysis"]["primary_category"]
        by_category.setdefault(cat, []).append(a)

    # Sort within each category
    for cat in by_category:
        by_category[cat] = _sort_articles(by_category[cat])

    total = len(articles)
    cats_with_articles = [c for c in CATEGORY_ORDER if c in by_category]

    sections_html = []
    for cat in cats_with_articles:
        cat_articles = by_category[cat]
        cards = []
        for a in cat_articles:
            analysis = a["analysis"]
            content = a["content"]
            source = a["source"]
            digest = a["digest"]

            universities = ", ".join(analysis.get("entities", {}).get("universities", []))
            bucket = digest.get("priority_bucket", "Low")
            css_class = f"priority-{bucket.lower()}"

            cards.append(f"""\
        <div class="article-card {css_class}">
          <div class="article-title"><a href="{escape(source['url'])}">{escape(content['title'])}</a></div>
          <div class="article-meta">{escape(universities or source['name'])} | {escape(content.get('published_at') or 'unknown')} | {bucket}</div>
          <div class="signal-summary">{escape(analysis.get('signal_summary') or '')}</div>
          <div class="sales-relevance">{escape(analysis.get('sales_relevance') or '')}</div>
        </div>""")

        sections_html.append(f"""\
    <div class="category-section">
      <div class="category-title">{escape(cat)}</div>
{''.join(cards)}
    </div>""")

    logo_uri = _load_logo_base64()
    logo_html = ""
    if logo_uri:
        logo_html = f'  <div class="logo"><img src="{logo_uri}" alt="Simovative"></div>\n'

    return f"""\
<!DOCTYPE html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Simovative HE Market Digest — {escape(run_date)}</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; color: #1a1a2e; }}
    .logo {{ text-align: center; margin-bottom: 24px; }}
    .logo img {{ max-width: 280px; height: auto; }}
    h1 {{ border-bottom: 3px solid #1a1a2e; padding-bottom: 12px; text-align: center; }}
    .summary {{ color: #555; margin-bottom: 32px; }}
    .category-section {{ margin: 32px 0; }}
    .category-title {{ font-size: 1.3em; font-weight: bold; color: #1a1a2e; border-bottom: 2px solid #ddd; padding-bottom: 6px; margin-bottom: 16px; }}
    .article-card {{ margin: 16px 0; padding: 12px 16px; border-left: 4px solid #4a90d9; background: #fafafa; }}
    .priority-high {{ border-left-color: #27ae60; }}
    .priority-medium {{ border-left-color: #f0c040; }}
    .priority-low {{ border-left-color: #b0b0b0; }}
    .article-title a {{ font-size: 1.1em; font-weight: bold; color: #1a1a2e; text-decoration: none; }}
    .article-title a:hover {{ text-decoration: underline; }}
    .article-meta {{ color: #666; font-size: 0.85em; margin: 4px 0 8px 0; }}
    .signal-summary {{ margin: 6px 0; }}
    .sales-relevance {{ color: #555; font-style: italic; font-size: 0.9em; }}
  </style>
</head>
<body>
{logo_html}  <h1>Simovative University News Digest — {escape(run_date)}</h1>
  <p class="summary">{total} Artikel | {len(cats_with_articles)} Kategorien</p>
{''.join(sections_html)}
</body>
</html>"""
