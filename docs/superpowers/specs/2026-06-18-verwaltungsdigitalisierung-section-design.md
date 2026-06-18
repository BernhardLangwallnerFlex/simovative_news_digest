# Verwaltungsdigitalisierung Section — Design Spec

> **Status:** APPROVED (design) — pending written-spec review
> **Date:** 2026-06-18
> **Origin:** Recipient request (NEGZ) to add public-administration / e-government sources to the news digest, derived from an NEGZ survey of how *Verwaltungsdigitalisierer:innen* inform themselves.

**Goal:** Add curated public-administration ("Verwaltungsdigitalisierung") sources to the pipeline and surface their content as a **separate section** in the existing email digest — distinct from, and below, the unchanged Hochschule digest. Routing between the two sections is driven by a second LLM relevance dimension, not by keyword filtering.

**Non-goals:** Do not alter the Hochschule digest's inclusion logic. Do not build podcast audio transcription. Do not ingest paywalled sources.

---

## 1. Scope decisions (settled during brainstorming)

| Decision | Outcome |
|----------|---------|
| Digest scope | **Separate Verwaltung section** in the same email. Hochschule digest unchanged. |
| Verwaltung inclusion filter | **Second LLM relevance dimension** (`verwaltung_relevance_score`), not keyword gating. |
| Source rollout | **RSS feeds + the easy HTML-crawl domains** (DStGB, Kommune21, move). Cookie-walled / paywalled sources parked in `TODO.md`. |
| Section ordering | Hochschule digest first, Verwaltung section below. |
| Taxonomy | Reuse the existing 9-category taxonomy for grouping in both sections. |

---

## 2. Sources

### 2.1 New RSS feeds → append to `RSS_FEEDS` in `config.py`
All URLs byte-verified (HTTP 200, valid RSS/Atom XML) on 2026-06-18.

| Source | Feed URL |
|--------|----------|
| Behörden Spiegel | `https://www.behoerden-spiegel.de/feed/` |
| eGovernment (Vogel IT-Medien) | `https://www.egovernment.de/rss/news.xml` |
| vdz – Verwaltung der Zukunft | `https://www.vdz.org/rss.xml` |
| eGovernment Podcast (Frenzel) | `https://egovernment-podcast.com/feed/mp3/` |
| Unbürokratisch (Podcast) | `https://feeds.captivate.fm/unbuerokratisch/` |
| Vitako "Verwaltung.Digital.Insights." (Podcast) | `https://verwaltungdigitalinsights-vitako-podcast.podigee.io/feed/mp3` |

Podcast feeds carry paragraph-length German show-notes in the item description/summary; `rss_crawler.py` already maps the entry summary into `raw_text`, so no audio transcription is needed.

### 2.2 NEGZ — switch from HTML-crawl to RSS
NEGZ currently appears in `MANDATORY_DOMAINS` as `https://negz.org/...` HTML targets. Replace with the verified feed `https://negz.org/feed/` (faster, more robust). Remove the NEGZ entries from `MANDATORY_DOMAINS`.

### 2.3 New HTML-crawl domains → append to `MANDATORY_DOMAINS` + add patterns
All three are server-rendered (no JS hurdle) and publish actively. **Kommune21 and move share the same CMS (K21 Media)** — identical article-URL shape.

| Source | Listing page to register | Article URL pattern | Excludes |
|--------|--------------------------|---------------------|----------|
| DStGB (press releases) | `https://www.dstgb.de/publikationen/pressemitteilungen/` | `https://www.dstgb.de/publikationen/pressemitteilungen/{slug}` | publication sub-indexes |
| DStGB (Aktuelles) | `https://www.dstgb.de/aktuelles/` | `https://www.dstgb.de/aktuelles/{YYYY-sem}/{slug}/` | `/aktuelles/archiv/...`, `/aktuelles/veranstaltungen/`, `/themen/...` |
| Kommune21 | `https://www.kommune21.de/` | `https://www.kommune21.de/k21-meldungen/{slug}` | `/k21-themen/...`, `/k21-heftarchiv/...`, `/k21-meldungen/feed` |
| move | `https://www.move-online.de/` | `https://www.move-online.de/k21-meldungen/{slug}` | `/k21-termine/...`, `/k21-themen/...`, `/k21-it-guide/...` |

For DStGB, both listings are registered as separate pattern entries. The `/aktuelles/` articles live under a dated `{YYYY-sem}` segment (e.g. `/aktuelles/2025-26/tag-der-bundeswehr-26/`), which the existing `{YYYY-sem}` placeholder (`\d{4}-\d+`) matches directly.

`{slug}` (single path segment) is used rather than `{long_slug}` because articles are already namespaced under a dedicated path (`/k21-meldungen/`, `/publikationen/pressemitteilungen/`), so a plain slug carries no risk of matching navigation links, and it also catches short 2-token slugs (e.g. `beliebter-chatbot`) that `{long_slug}` would miss.

### 2.4 Parked sources → new `TODO.md`
| Source | Reason parked |
|--------|---------------|
| Digitale Verwaltung (BMI / CIO Bund) | Government GSB CMS behind a cookie-check wall; RSS XML endpoint not resolvable. Needs Playwright cookie-wall handling. |
| Innovative Verwaltung | Springer Professional, teaser paywall, no public feed. |
| Tagesspiegel Background (Digitalisierung & KI) | Full paywall + Cloudflare 403 (~€139–199/license/month). Free *Wissenschaft* feed already in config. |
| Vitako "VITAKO aktuell" magazine (PDF archive) | Optional future text source (PDF extraction). |

---

## 3. Classifier change — second relevance dimension

File: `src/processing/classifier.py`.

- Add a new analysis field **`verwaltung_relevance_score`** (float 0.0–1.0): relevance to public-administration digitalization (OZG, Registermodernisierung, e-government, KI in der Verwaltung, kommunale/Länder-/Bundes-IT), **independent of** any university angle.
- Extend `USER_PROMPT_TEMPLATE` to request this field; extend `SYSTEM_PROMPT` to explain the two relevance dimensions are scored independently (an article can be high on one and low on the other).
- Extend `_validate_llm_output` to validate `verwaltung_relevance_score` as a 0–1 float. **Backward compatibility:** treat a missing field as `0.0` rather than failing validation, so re-runs over historical `articles_classified.json` don't break.
- Reuse existing fields (`primary_category`, `signal_summary`, `confidence_score`) for both sections. The university-specific `sales_relevance` field is simply not rendered in the Verwaltung section.
- **`primary_category` must be assigned by signal type for BOTH audiences.** The original Hochschule-framed prompt binned pure public-administration articles as "Irrelevant" (verified live: an OZG/Registermodernisierung article scored verwaltung=0.98 but category="Irrelevant"), which the routing/render then dropped. The system prompt is rewritten so the taxonomy applies to higher-education AND public-administration articles alike, and "Irrelevant" means relevant to *neither* dimension. Both `filter_for_digest` and `filter_for_verwaltung` exclude `{Research News, Irrelevant}` — neither has a renderable category section.
- Computed uniformly for **all** articles (one extra output field; negligible token cost). No source tagging needed for routing.

---

## 4. Routing — exactly one section per article, Hochschule has priority

A single article is assigned to at most one section.

**Hochschule digest** (existing `filter_for_digest`, unchanged):
`relevance_score >= 0.6` AND `confidence_score >= 0.6` AND `primary_category` not in {"Research News", "Irrelevant"}.

**Verwaltung section** (new `filter_for_verwaltung`):
NOT already selected for the Hochschule digest
AND `verwaltung_relevance_score >= VERWALTUNG_RELEVANCE_THRESHOLD` (default `0.6`, in `config.py`)
AND `confidence_score >= 0.6`
AND `primary_category != "Irrelevant"`.

The Verwaltung section is intentionally **not** Hochschule-filtered — it is the broader stream the recipient asked for.

---

## 5. Pipeline & rendering

**`main.py`** (Step 6+ region):
1. After classification, build `hochschule_articles = filter_for_digest(classified)`.
2. Build `verwaltung_articles = filter_for_verwaltung(classified, exclude=hochschule_articles)`.
3. Run `deduplicate_near_duplicates` on each set independently.
4. Render one combined HTML email (both sections).
5. Stats dict gains `verwaltung_included`. History update is unchanged (still keyed on all classified articles).

**`src/digest/html_generator.py`**:
- New `filter_for_verwaltung(classified, exclude)` mirroring `filter_for_digest`.
- `generate_html_digest` renders the Verwaltung block beneath the Hochschule digest, grouped by the same taxonomy categories, under a clear heading ("Verwaltungsdigitalisierung"). If the Verwaltung set is empty, the section is omitted entirely (no empty header).

**`config.py`**: add `VERWALTUNG_RELEVANCE_THRESHOLD = 0.6`.

---

## 6. Known limitation / verification step

- The `LinkClassifier` fallback (`src/crawlers/link_classifier.py`) uses Hochschule-specific reference descriptions ("Pressemitteilung einer Hochschule", "Studienangebot Bewerbung", …). For the new public-administration domains, this fallback is weaker. **Mitigation:** we author explicit URL patterns (section 2.3), which is the deterministic primary path and does not depend on the classifier. Broadening the classifier's reference set to include public-administration article descriptions is an *optional* follow-up, not required for this work.
- **Implementation must verify each new crawl domain with a live test crawl** (run the domain crawler against DStGB / Kommune21 / move and confirm `articles > 0` via the `discovery_method = "pattern"` path). The patterns in 2.3 are derived from a live inspection of the listing pages on 2026-06-18 but must be confirmed end-to-end against the actual scrape + date filter.

---

## 7. Component summary (what / interface / depends-on)

| Unit | Does | Depends on |
|------|------|-----------|
| `config.py` | Source lists + `VERWALTUNG_RELEVANCE_THRESHOLD` | — |
| `news_article_patterns.json` | URL patterns for 3 new domains | crawler placeholder grammar |
| `classifier.py` | Adds `verwaltung_relevance_score` to analysis | OpenAI |
| `html_generator.py` | `filter_for_verwaltung` + Verwaltung render block | classified articles |
| `main.py` | Split → near-dedup per section → combined render | above |
| `TODO.md` | Records parked sources + reasons | — |

## 8. Testing

- **Classifier:** unit test that an article scored high on Verwaltung relevance but low on Hochschule relevance is routed to the Verwaltung set only; and vice-versa; and that a missing `verwaltung_relevance_score` defaults to 0.0 without validation failure.
- **Routing:** unit test `filter_for_verwaltung` excludes anything already in the Hochschule set.
- **Crawler:** live test crawl per new domain asserting pattern-based discovery yields ≥1 article.
- **End-to-end:** one full local `python main.py` run; inspect the generated HTML for a populated Verwaltung section.
