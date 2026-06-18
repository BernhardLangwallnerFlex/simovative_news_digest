# Verwaltungsdigitalisierung Section Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add curated public-administration sources to the pipeline and render their relevant articles as a separate "Verwaltungsdigitalisierung" section beneath the unchanged Hochschule digest, routed by a new LLM relevance dimension.

**Architecture:** The classifier emits a second relevance score (`verwaltung_relevance_score`) per article. `main.py` splits classified articles into a Hochschule set (existing logic, priority) and a Verwaltung set (new `filter_for_verwaltung`), near-dedups each independently, and renders one combined HTML email. New RSS feeds are appended to `config.py`; three new HTML-crawl domains get URL-pattern entries in `news_article_patterns.json`.

**Tech Stack:** Python 3.11, OpenAI API, feedparser, Playwright + BeautifulSoup (existing crawler), pytest (newly added for unit tests).

Spec: `docs/superpowers/specs/2026-06-18-verwaltungsdigitalisierung-section-design.md`

---

## File Structure

| File | Responsibility | Change |
|------|---------------|--------|
| `requirements.txt` | deps | add `pytest` |
| `tests/conftest.py` | put project root on `sys.path` for tests | create |
| `tests/test_filters.py` | unit tests for routing filters | create |
| `tests/test_classifier_validation.py` | unit tests for output validation | create |
| `src/processing/normalizer.py` | canonical article schema | add `verwaltung_relevance_score` field |
| `src/processing/classifier.py` | LLM classification | add 2nd relevance dimension |
| `src/digest/html_generator.py` | filtering + rendering | add `filter_for_verwaltung` + Verwaltung render block (refactor card rendering) |
| `config.py` | source lists + threshold | add feeds/domains + `VERWALTUNG_RELEVANCE_THRESHOLD` |
| `news_article_patterns.json` | crawl URL patterns | add 4 entries (DStGB ×2, Kommune21, move) |
| `main.py` | pipeline orchestration | route into two sections |
| `scripts/test_verwaltung_crawl.py` | live crawl smoke test | create |
| `TODO.md` | parked sources | create |

---

### Task 0: Feature branch

- [ ] **Step 1: Create and switch to a feature branch**

We are on `master`; isolate the work.

Run:
```bash
git checkout -b feature/verwaltungsdigitalisierung-section
```
Expected: `Switched to a new branch 'feature/verwaltungsdigitalisierung-section'`

---

### Task 1: Test harness (pytest)

**Files:**
- Modify: `requirements.txt`
- Create: `tests/conftest.py`

- [ ] **Step 1: Add pytest to requirements**

Append this line to `requirements.txt`:
```
pytest>=8.0.0
```

- [ ] **Step 2: Install it**

Run:
```bash
.venv/bin/python -m pip install "pytest>=8.0.0"
```
Expected: `Successfully installed pytest-...`

- [ ] **Step 3: Create conftest so tests can import `config` and `src`**

Create `tests/conftest.py`:
```python
import os
import sys

# Put the project root on sys.path so tests can `import config` and `from src...`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

- [ ] **Step 4: Verify pytest collects with zero tests**

Run:
```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: `no tests ran` (exit code 5) — confirms collection works without import errors.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt tests/conftest.py
git commit -m "test: add pytest harness"
```

---

### Task 2: Normalizer — add `verwaltung_relevance_score` field

**Files:**
- Modify: `src/processing/normalizer.py` (the `analysis` dict, ~line 99-117)

- [ ] **Step 1: Add the field to the canonical analysis schema**

In `src/processing/normalizer.py`, inside the `analysis` dict, add `verwaltung_relevance_score` next to `relevance_score`:
```python
        "analysis": {
            "processed": False,
            "taxonomy_version": "1.0",
            "primary_category": None,
            "secondary_tags": [],
            "relevance_score": None,
            "verwaltung_relevance_score": None,
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
```

- [ ] **Step 2: Verify import still works**

Run:
```bash
.venv/bin/python -c "from src.processing.normalizer import normalize_article; print('ok')"
```
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add src/processing/normalizer.py
git commit -m "feat: add verwaltung_relevance_score to article schema"
```

---

### Task 3: Classifier — second relevance dimension

**Files:**
- Modify: `src/processing/classifier.py`
- Test: `tests/test_classifier_validation.py`

- [ ] **Step 1: Write failing tests for `_validate_llm_output`**

Create `tests/test_classifier_validation.py`:
```python
from src.processing.classifier import _validate_llm_output


def _base():
    return {
        "primary_category": "Digital Strategy & IT Initiatives",
        "relevance_score": 0.5,
        "priority_score": 0.5,
        "confidence_score": 0.8,
        "entities": {},
    }


def test_missing_verwaltung_score_is_allowed():
    # Backward compat: historical records have no verwaltung_relevance_score.
    assert _validate_llm_output(_base()) is True


def test_valid_verwaltung_score_passes():
    data = _base()
    data["verwaltung_relevance_score"] = 0.7
    assert _validate_llm_output(data) is True


def test_out_of_range_verwaltung_score_fails():
    data = _base()
    data["verwaltung_relevance_score"] = 2.0
    assert _validate_llm_output(data) is False


def test_non_numeric_verwaltung_score_fails():
    data = _base()
    data["verwaltung_relevance_score"] = "high"
    assert _validate_llm_output(data) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
.venv/bin/python -m pytest tests/test_classifier_validation.py -v
```
Expected: `test_out_of_range_verwaltung_score_fails` and `test_non_numeric_verwaltung_score_fails` FAIL (current validator ignores the field, so they wrongly pass → assertion error). The two "allowed/passes" tests pass.

- [ ] **Step 3: Update `_validate_llm_output`**

In `src/processing/classifier.py`, replace the body of `_validate_llm_output` with:
```python
def _validate_llm_output(data: dict) -> bool:
    if data.get("primary_category") not in ALLOWED_CATEGORIES:
        return False
    for score_field in ("relevance_score", "priority_score", "confidence_score"):
        val = data.get(score_field)
        if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
            return False
    # verwaltung_relevance_score is optional (older records lack it); if present it must be valid.
    vr = data.get("verwaltung_relevance_score")
    if vr is not None and (not isinstance(vr, (int, float)) or not (0.0 <= float(vr) <= 1.0)):
        return False
    if not isinstance(data.get("entities"), dict):
        return False
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/test_classifier_validation.py -v
```
Expected: 4 passed.

- [ ] **Step 5: Persist the new field in `classify_article`**

In `src/processing/classifier.py`, inside `classify_article`, add `verwaltung_relevance_score` to the `article["analysis"].update({...})` call, right after `relevance_score`:
```python
                article["analysis"].update({
                    "processed": True,
                    "primary_category": data["primary_category"],
                    "secondary_tags": data.get("secondary_tags", [])[:5],
                    "relevance_score": float(data["relevance_score"]),
                    "verwaltung_relevance_score": float(data.get("verwaltung_relevance_score") or 0.0),
                    "priority_score": float(data["priority_score"]),
                    "confidence_score": float(data["confidence_score"]),
                    "entities": data.get("entities", {}),
                    "signal_summary": data.get("signal_summary"),
                    "sales_relevance": data.get("sales_relevance"),
                })
```

- [ ] **Step 6: Extend the prompts**

In `src/processing/classifier.py`, replace `SYSTEM_PROMPT` with (adds one sentence about the two independent scores):
```python
SYSTEM_PROMPT = """\
You are a structured information extraction system for university market intelligence.
You classify German-language news articles according to a fixed taxonomy relevant \
to higher education institutions in the DACH region (Germany, Austria, Switzerland).
If the article concerns a university outside the DACH region, classify it as "Irrelevant". If the article only talks about research budgets, classify it as "Research News".
You score two INDEPENDENT relevance dimensions: "relevance_score" (relevance to the \
higher-education / university market) and "verwaltung_relevance_score" (relevance to \
public-administration digitalization in the DACH region — e-government, OZG, \
Registermodernisierung, municipal/state/federal IT — regardless of any university angle). \
An article may score high on one and low on the other.
You must strictly follow the allowed category list and output valid JSON only.
Do not include any text outside the JSON object."""
```

Then in `USER_PROMPT_TEMPLATE`, add the new field line directly after the `relevance_score` line:
```python
- "relevance_score": float 0.0-1.0 (how relevant to university market intelligence)
- "verwaltung_relevance_score": float 0.0-1.0 (how relevant to public-administration / e-government digitalization in DACH — OZG, Registermodernisierung, municipal/state/federal IT — independent of any university angle)
```

- [ ] **Step 7: Verify import + full test run**

Run:
```bash
.venv/bin/python -c "from src.processing.classifier import classify_article, _validate_llm_output; print('ok')"
.venv/bin/python -m pytest tests/ -q
```
Expected: `ok`, then all tests pass.

- [ ] **Step 8: Commit**

```bash
git add src/processing/classifier.py tests/test_classifier_validation.py
git commit -m "feat: classify verwaltung relevance as a second dimension"
```

---

### Task 4: Routing filter — `filter_for_verwaltung`

**Files:**
- Modify: `src/digest/html_generator.py`
- Modify: `config.py` (threshold constant — minimal addition here; full source lists in Task 6)
- Test: `tests/test_filters.py`

- [ ] **Step 1: Add the threshold constant to config**

In `config.py`, add near `ARTICLES_PER_DOMAIN`:
```python
# Minimum verwaltung_relevance_score for an article to appear in the
# Verwaltungsdigitalisierung section of the digest.
VERWALTUNG_RELEVANCE_THRESHOLD = 0.6
```

- [ ] **Step 2: Write failing tests**

Create `tests/test_filters.py`:
```python
from src.digest.html_generator import filter_for_digest, filter_for_verwaltung


def make_article(article_id, *, processed=True,
                 category="Digital Strategy & IT Initiatives",
                 relevance=0.0, verwaltung=0.0, confidence=0.9, priority=0.5):
    return {
        "article_id": article_id,
        "source": {"name": "test", "url": f"https://example.com/{article_id}"},
        "content": {"title": "t", "raw_text": "x", "published_at": "2026-06-01"},
        "analysis": {
            "processed": processed,
            "primary_category": category,
            "relevance_score": relevance,
            "verwaltung_relevance_score": verwaltung,
            "confidence_score": confidence,
            "priority_score": priority,
            "entities": {"universities": []},
            "signal_summary": "s",
            "sales_relevance": "sr",
        },
        "digest": {"included": False, "priority_bucket": None},
    }


def test_verwaltung_only_article_routes_to_verwaltung():
    a = make_article("v1", relevance=0.1, verwaltung=0.8)
    assert filter_for_digest([a]) == []
    assert filter_for_verwaltung([a], exclude=[]) == [a]


def test_hochschule_takes_priority_over_verwaltung():
    a = make_article("h1", relevance=0.8, verwaltung=0.8)
    hs = filter_for_digest([a])
    assert hs == [a]
    # Already in the Hochschule set → must not also appear in Verwaltung.
    assert filter_for_verwaltung([a], exclude=hs) == []


def test_verwaltung_below_threshold_excluded():
    a = make_article("v2", relevance=0.1, verwaltung=0.4)
    assert filter_for_verwaltung([a], exclude=[]) == []


def test_irrelevant_category_excluded_from_verwaltung():
    a = make_article("v3", category="Irrelevant", verwaltung=0.9)
    assert filter_for_verwaltung([a], exclude=[]) == []


def test_unprocessed_article_excluded_from_verwaltung():
    a = make_article("v4", processed=False, verwaltung=0.9)
    assert filter_for_verwaltung([a], exclude=[]) == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run:
```bash
.venv/bin/python -m pytest tests/test_filters.py -v
```
Expected: import error / FAIL — `filter_for_verwaltung` does not exist yet.

- [ ] **Step 4: Implement `filter_for_verwaltung`**

In `src/digest/html_generator.py`, add after `filter_for_digest` (it reuses `_priority_bucket`):
```python
def filter_for_verwaltung(articles: list[dict], exclude: list[dict]) -> list[dict]:
    """Select articles for the Verwaltungsdigitalisierung section.

    Include if NOT already in the Hochschule digest (`exclude`) AND
    verwaltung_relevance_score >= threshold AND confidence_score >= 0.6 AND
    primary_category != "Irrelevant". Intentionally NOT Hochschule-filtered.
    """
    from config import VERWALTUNG_RELEVANCE_THRESHOLD

    excluded_ids = {a["article_id"] for a in exclude}
    included = []
    for a in articles:
        analysis = a.get("analysis", {})
        if not analysis.get("processed"):
            continue
        if a["article_id"] in excluded_ids:
            continue
        if analysis.get("primary_category", "") == "Irrelevant":
            continue
        if (analysis.get("verwaltung_relevance_score") or 0) < VERWALTUNG_RELEVANCE_THRESHOLD:
            continue
        if (analysis.get("confidence_score") or 0) < 0.6:
            continue

        bucket = _priority_bucket(analysis.get("priority_score") or 0)
        a["digest"]["included"] = True
        a["digest"]["priority_bucket"] = bucket
        included.append(a)

    logger.info("Verwaltung filter: %d articles included", len(included))
    return included
```

- [ ] **Step 5: Run tests to verify they pass**

Run:
```bash
.venv/bin/python -m pytest tests/test_filters.py -v
```
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add src/digest/html_generator.py config.py tests/test_filters.py
git commit -m "feat: add filter_for_verwaltung routing with Hochschule priority"
```

---

### Task 5: Render the Verwaltung section

**Files:**
- Modify: `src/digest/html_generator.py`

This refactors the card/section rendering out of `generate_html_digest` so both sections share it, then adds the Verwaltung block.

- [ ] **Step 1: Extract card + section rendering helpers**

In `src/digest/html_generator.py`, add these two helpers (place them above `generate_html_digest`). They lift the existing inline rendering, adding a `show_sales_relevance` flag (the `sales_relevance` line is Hochschule-only):
```python
def _render_article_card(a: dict, show_sales_relevance: bool) -> str:
    analysis = a["analysis"]
    content = a["content"]
    source = a["source"]
    digest = a["digest"]

    universities = ", ".join(analysis.get("entities", {}).get("universities", []))
    bucket = digest.get("priority_bucket", "Low")
    css_class = f"priority-{bucket.lower()}"

    sales_html = ""
    if show_sales_relevance:
        sales_html = (
            f'          <div class="sales-relevance">'
            f'{escape(analysis.get("sales_relevance") or "")}</div>\n'
        )

    return f"""\
        <div class="article-card {css_class}">
          <div class="article-title"><a href="{escape(source['url'])}">{escape(content['title'])}</a></div>
          <div class="article-meta">{escape(universities or source['name'])} | {escape(content.get('published_at') or 'unknown')} | {bucket}</div>
          <div class="signal-summary">{escape(analysis.get('signal_summary') or '')}</div>
{sales_html}{_render_also_reported(a)}        </div>"""


def _render_category_sections(articles: list[dict], show_sales_relevance: bool = True) -> tuple[str, int]:
    """Group articles by category (fixed order) and render section HTML.

    Returns (sections_html, number_of_categories_with_articles).
    """
    by_category: dict[str, list[dict]] = {}
    for a in articles:
        cat = a["analysis"]["primary_category"]
        by_category.setdefault(cat, []).append(a)
    for cat in by_category:
        by_category[cat] = _sort_articles(by_category[cat])

    cats_with_articles = [c for c in CATEGORY_ORDER if c in by_category]
    sections_html = []
    for cat in cats_with_articles:
        cards = [_render_article_card(a, show_sales_relevance) for a in by_category[cat]]
        sections_html.append(f"""\
    <div class="category-section">
      <div class="category-title">{escape(cat)}</div>
{''.join(cards)}
    </div>""")
    return "".join(sections_html), len(cats_with_articles)
```

- [ ] **Step 2: Rewrite `generate_html_digest` to use the helpers and append the Verwaltung block**

Replace the entire `generate_html_digest` function body with:
```python
def generate_html_digest(
    articles: list[dict],
    run_date: str,
    verwaltung_articles: list[dict] | None = None,
) -> str:
    """Render the Hochschule digest, plus an optional Verwaltung section below it."""
    hochschule_html, hs_cats = _render_category_sections(articles, show_sales_relevance=True)

    verwaltung_block = ""
    if verwaltung_articles:
        v_html, _ = _render_category_sections(verwaltung_articles, show_sales_relevance=False)
        verwaltung_block = f"""\
  <h2 class="verwaltung-heading">Verwaltungsdigitalisierung</h2>
  <p class="summary">{len(verwaltung_articles)} Artikel</p>
{v_html}"""

    total = len(articles)

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
    .verwaltung-heading {{ margin-top: 48px; border-bottom: 3px solid #1a1a2e; padding-bottom: 12px; text-align: center; }}
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
    .also-reported {{ color: #888; font-size: 0.85em; margin-top: 6px; font-style: italic; }}
    .also-reported a {{ color: #4a90d9; text-decoration: none; }}
    .also-reported a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
{logo_html}  <h1>Simovative University News Digest — {escape(run_date)}</h1>
  <p class="summary">{total} Artikel | {hs_cats} Kategorien</p>
{hochschule_html}{verwaltung_block}
</body>
</html>"""
```

- [ ] **Step 3: Smoke-test rendering (both sections, and Verwaltung-omitted)**

Run:
```bash
.venv/bin/python - <<'PY'
from src.digest.html_generator import generate_html_digest
from tests.test_filters import make_article
hs = make_article("h", relevance=0.9, verwaltung=0.1); hs["digest"]["priority_bucket"]="High"
v = make_article("v", relevance=0.1, verwaltung=0.9); v["digest"]["priority_bucket"]="Medium"
html = generate_html_digest([hs], run_date="2026-06-18", verwaltung_articles=[v])
assert "Verwaltungsdigitalisierung" in html
assert "verwaltung-heading" in html
# Verwaltung omitted when empty:
html2 = generate_html_digest([hs], run_date="2026-06-18", verwaltung_articles=[])
assert "Verwaltungsdigitalisierung" not in html2
print("render ok")
PY
```
Expected: `render ok`

- [ ] **Step 4: Commit**

```bash
git add src/digest/html_generator.py
git commit -m "feat: render Verwaltungsdigitalisierung section in digest"
```

---

### Task 6: Sources in config

**Files:**
- Modify: `config.py`

- [ ] **Step 1: Append the new RSS feeds (incl. NEGZ)**

In `config.py`, append these entries to the `RSS_FEEDS` list (before the closing `]`):
```python
    # --- Verwaltungsdigitalisierung (public administration) ---
    "https://www.behoerden-spiegel.de/feed/",
    "https://www.egovernment.de/rss/news.xml",
    "https://www.vdz.org/rss.xml",
    "https://egovernment-podcast.com/feed/mp3/",
    "https://feeds.captivate.fm/unbuerokratisch/",
    "https://verwaltungdigitalinsights-vitako-podcast.podigee.io/feed/mp3",
    "https://negz.org/feed/",
```

- [ ] **Step 2: Remove the NEGZ HTML-crawl entry from `MANDATORY_DOMAINS`**

In `config.py`, delete this line from `MANDATORY_DOMAINS` (NEGZ is now an RSS feed):
```python
    "https://negz.org/neuigkeiten-aus-dem-negz/",
```

- [ ] **Step 3: Append the new HTML-crawl domains to `MANDATORY_DOMAINS`**

Add these entries to `MANDATORY_DOMAINS` (before the closing `]`):
```python
    "https://www.dstgb.de/publikationen/pressemitteilungen/",
    "https://www.dstgb.de/aktuelles/",
    "https://www.kommune21.de/",
    "https://www.move-online.de/",
```

- [ ] **Step 4: Verify config imports and the lists look right**

Run:
```bash
.venv/bin/python -c "import config; print('negz feed in RSS:', 'https://negz.org/feed/' in config.RSS_FEEDS); print('negz crawl removed:', 'https://negz.org/neuigkeiten-aus-dem-negz/' not in config.MANDATORY_DOMAINS); print('threshold:', config.VERWALTUNG_RELEVANCE_THRESHOLD); print('domains+4:', sum(d in config.MANDATORY_DOMAINS for d in ['https://www.dstgb.de/publikationen/pressemitteilungen/','https://www.dstgb.de/aktuelles/','https://www.kommune21.de/','https://www.move-online.de/']))"
```
Expected: `negz feed in RSS: True`, `negz crawl removed: True`, `threshold: 0.6`, `domains+4: 4`

- [ ] **Step 5: Commit**

```bash
git add config.py
git commit -m "feat: add public-administration RSS feeds and crawl domains"
```

---

### Task 7: URL patterns for the new crawl domains

**Files:**
- Modify: `news_article_patterns.json`

Patterns derived from a live inspection of the listing pages on 2026-06-18. Kommune21 and move share the K21 Media CMS (`/k21-meldungen/{slug}`). The metadata keys (`source`, `cms`, `example`) are ignored by the loader; only `article_url_pattern*`, `exclude_url_patterns`, and `wait_for_selector` are read.

- [ ] **Step 1: Add the four entries**

In `news_article_patterns.json`, add these four key/value pairs inside the top-level `"patterns"` object:
```json
    "https://www.dstgb.de/publikationen/pressemitteilungen/": {
      "source": "DStGB Pressemitteilungen",
      "article_url_pattern": "https://www.dstgb.de/publikationen/pressemitteilungen/{slug}",
      "example": "https://www.dstgb.de/publikationen/pressemitteilungen/kommunale-handlungsfaehigkeit-garantieren-und-sichern/",
      "cms": "custom"
    },
    "https://www.dstgb.de/aktuelles/": {
      "source": "DStGB Aktuelles",
      "article_url_pattern": "https://www.dstgb.de/aktuelles/{YYYY-sem}/{slug}/",
      "example": "https://www.dstgb.de/aktuelles/2025-26/tag-der-bundeswehr-26/",
      "cms": "custom"
    },
    "https://www.kommune21.de/": {
      "source": "Kommune21",
      "article_url_pattern": "https://www.kommune21.de/k21-meldungen/{slug}",
      "exclude_url_patterns": [
        "https://www.kommune21.de/k21-meldungen/feed"
      ],
      "example": "https://www.kommune21.de/k21-meldungen/arbeitshilfen-zu-ki-in-der-verwaltung/",
      "cms": "K21Media"
    },
    "https://www.move-online.de/": {
      "source": "move - moderne verwaltung",
      "article_url_pattern": "https://www.move-online.de/k21-meldungen/{slug}",
      "exclude_url_patterns": [
        "https://www.move-online.de/k21-meldungen/feed"
      ],
      "example": "https://www.move-online.de/k21-meldungen/ki-in-der-justiz/",
      "cms": "K21Media"
    },
```

- [ ] **Step 2: Verify the JSON is valid and patterns compile**

Run:
```bash
.venv/bin/python -c "
import json
json.load(open('news_article_patterns.json'))
from src.crawlers.university_domain_crawler import _load_patterns
p=_load_patterns()
for k in ['https://www.dstgb.de/publikationen/pressemitteilungen','https://www.dstgb.de/aktuelles','https://www.kommune21.de','https://www.move-online.de']:
    assert k in p, ('missing '+k)
    print(k, '->', [pat.pattern for pat in p[k]['patterns']])
print('patterns ok')
"
```
Expected: prints a compiled regex per domain and `patterns ok` (keys are stored trailing-slash-stripped).

- [ ] **Step 3: Commit**

```bash
git add news_article_patterns.json
git commit -m "feat: add crawl URL patterns for DStGB, Kommune21, move"
```

---

### Task 8: Wire routing into the pipeline

**Files:**
- Modify: `main.py`

- [ ] **Step 1: Import the new filter**

In `main.py`, update the html_generator import:
```python
from src.digest.html_generator import filter_for_digest, filter_for_verwaltung, generate_html_digest
```

- [ ] **Step 2: Add the stat key**

In `main.py`, add `"verwaltung_included": 0,` to the `stats` dict (next to `"digest_included": 0,`).

- [ ] **Step 3: Replace the Step 6+7 digest block with two-section routing**

In `main.py`, replace the block that currently runs from `digest_articles = filter_for_digest(classified)` through the `generate_html_digest(...)` call (the "Step 6+7" / "Step 6.5" region) with:
```python
    # ── Step 6+7: Digest Generation (two sections) ──────────────────
    logger.info("Step 6+7: Generating digest")
    digest_articles = filter_for_digest(classified)
    verwaltung_articles = filter_for_verwaltung(classified, exclude=digest_articles)
    logger.info(
        "Routing: %d Hochschule articles, %d Verwaltung articles",
        len(digest_articles), len(verwaltung_articles),
    )

    # -- Step 6.5: Near-duplicate detection (per section) --
    logger.info("Step 6.5: Near-duplicate detection (Hochschule=%d, Verwaltung=%d)",
                len(digest_articles), len(verwaltung_articles))
    digest_articles = deduplicate_near_duplicates(digest_articles)
    verwaltung_articles = deduplicate_near_duplicates(verwaltung_articles)

    stats["digest_included"] = len(digest_articles)
    stats["verwaltung_included"] = len(verwaltung_articles)

    # ── Source Transparency Report ──────────────────────────────────
    generate_source_transparency_report(normalized, new_articles, digest_articles, run_date)

    html = generate_html_digest(
        digest_articles, run_date=run_date, verwaltung_articles=verwaltung_articles
    )
    out_path = digest_path(run_date)
    out_path.write_text(html, encoding="utf-8")
    logger.info(
        "Digest written to %s (Hochschule=%d, Verwaltung=%d)",
        out_path, len(digest_articles), len(verwaltung_articles),
    )
```

Note: the lines after this (the `if EMAIL_RECIPIENTS:` email block) stay unchanged and continue to send `html`.

- [ ] **Step 4: Verify main.py imports cleanly**

Run:
```bash
.venv/bin/python -c "import ast; ast.parse(open('main.py').read()); print('main.py parses')"
.venv/bin/python -c "import main; print('main imports ok')"
```
Expected: `main.py parses`, then `main imports ok`.

- [ ] **Step 5: Commit**

```bash
git add main.py
git commit -m "feat: route classified articles into Hochschule + Verwaltung sections"
```

---

### Task 9: Live crawl smoke test for the new domains

**Files:**
- Create: `scripts/test_verwaltung_crawl.py`

This mirrors the existing `test_domain_crawler.py` convention (standalone runnable script, real network).

- [ ] **Step 1: Create the script**

Create `scripts/test_verwaltung_crawl.py`:
```python
"""Live smoke test: confirm pattern-based discovery works for the new
public-administration crawl domains. Requires network + Playwright.

Run: .venv/bin/python scripts/test_verwaltung_crawl.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.crawlers.university_domain_crawler import crawl_university_domain

DOMAINS = [
    "https://www.dstgb.de/publikationen/pressemitteilungen/",
    "https://www.dstgb.de/aktuelles/",
    "https://www.kommune21.de/",
    "https://www.move-online.de/",
]


def main():
    failures = []
    for domain in DOMAINS:
        print(f"\n=== {domain} ===")
        articles = crawl_university_domain(domain, max_articles=10, days_back=0)
        methods = {a.get("discovery_method") for a in articles}
        print(f"  {len(articles)} articles | methods={methods}")
        for a in articles[:5]:
            print(f"   - {a['title'][:70]} | {a['url']}")
        if not articles:
            failures.append(domain)
        elif "pattern" not in methods:
            print(f"  WARNING: no pattern-based discovery (fell back to {methods})")

    if failures:
        print(f"\nFAILED — zero articles for: {failures}")
        sys.exit(1)
    print("\nAll domains yielded articles.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the smoke test**

Run:
```bash
.venv/bin/python scripts/test_verwaltung_crawl.py
```
Expected: each domain prints `>0 articles` with `methods={'pattern'}` and sample titles; final line `All domains yielded articles.` (exit 0).

If a domain yields zero or falls back to the classifier, the pattern is wrong — re-inspect that listing page's article hrefs (`curl -sL <listing> | grep -oE 'href="[^"]+"'`) and correct the `article_url_pattern` in `news_article_patterns.json`, then re-run. Do not proceed until all four yield articles via `pattern`.

- [ ] **Step 3: Commit**

```bash
git add scripts/test_verwaltung_crawl.py
git commit -m "test: live crawl smoke test for verwaltung domains"
```

---

### Task 10: TODO.md for parked sources

**Files:**
- Create: `TODO.md`

- [ ] **Step 1: Create TODO.md**

Create `TODO.md`:
```markdown
# TODO — Parked Sources

Sources from the NEGZ Verwaltungsdigitalisierung request (2026-06) that were
intentionally deferred. Not forgotten — revisit when capacity allows.

## Parked

- **Digitale Verwaltung (BMI / CIO Bund)** — `https://www.digitale-verwaltung.de/`
  Government GSB CMS behind a cookie-check wall; the RSS XML endpoint is not
  resolvable via plain HTTP. Needs Playwright cookie-wall handling (the existing
  `dismiss_cookies` helper may suffice — try registering the "Meldungen" node as
  a crawl domain and confirm the cookie wall is dismissed). Article listing:
  `https://www.digitale-verwaltung.de/Webs/DV/DE/aktuelles-service/aktuelles-service-node.html`.

- **Innovative Verwaltung** — Springer Professional
  (`https://www.springerprofessional.de/innovative-verwaltung/5030910`).
  Teaser paywall; no public RSS feed found. Only title+abstract are public.
  Revisit only if a teaser-level feed is confirmed.

- **Tagesspiegel Background (Digitalisierung & KI)** —
  `https://background.tagesspiegel.de/digitalisierung-und-ki`. Full paywall +
  Cloudflare 403; ~€139–199/license/month. The free *Wissenschaft* RSS feed is
  already in `RSS_FEEDS`.

- **VITAKO aktuell (print magazine PDFs)** — `https://vitako.de/archive/category/aktuelles/vitako-aktuell`.
  Quarterly magazine published as PDF (distinct from the Vitako podcast, which is
  already ingested). Optional future text source; would need PDF text extraction.
```

- [ ] **Step 2: Commit**

```bash
git add TODO.md
git commit -m "docs: record parked verwaltung sources"
```

---

### Task 11: End-to-end verification

- [ ] **Step 1: Full unit test run**

Run:
```bash
.venv/bin/python -m pytest tests/ -v
```
Expected: all tests pass.

- [ ] **Step 2: Full pipeline run (network + OpenAI + Playwright)**

Run:
```bash
source .venv/bin/activate && python main.py
```
Expected: completes (exit 0); log shows `Routing: N Hochschule articles, M Verwaltung articles` with M > 0, and `Digest written to ...`.

- [ ] **Step 3: Inspect the generated digest for the Verwaltung section**

Run (adjust date if the run date differs):
```bash
ls -t /tmp/news_digest/processed/*/digest_*.html | head -1 | xargs grep -c "Verwaltungsdigitalisierung"
```
Expected: `1` (the section heading is present). Open the file in a browser to eyeball the section content and ordering.

- [ ] **Step 4: Final review and merge decision**

Stop here and report results to the user (article counts per section, any zero-yield domains, the digest path). Do not merge to `master` without explicit approval. The branch `feature/verwaltungsdigitalisierung-section` holds all commits.

---

## Self-Review notes

- **Spec coverage:** sources (Tasks 6–7), 2nd relevance dimension (Tasks 2–3), routing/priority (Task 4), separate section render (Task 5), pipeline wiring (Task 8), crawl-pattern verification (Task 9), parked sources/TODO (Task 10), threshold in config (Task 4). All spec sections map to a task.
- **Backward compat:** missing `verwaltung_relevance_score` defaults to 0.0 (Task 3 Step 3 + validation Step 3) so re-runs over historical `articles_classified.json` don't break.
- **Type consistency:** `filter_for_verwaltung(articles, exclude)`, `generate_html_digest(articles, run_date, verwaltung_articles=None)`, `_render_category_sections(...) -> (str, int)` used consistently across Tasks 4, 5, 8.
- **No keyword filtering** is introduced — routing is purely score-based per the approved design.
