# Simovative University News Digest System

## Technical Specification v1.0

------------------------------------------------------------------------

# 1. System Overview

The system is a **stateful, cron-triggered university market
intelligence pipeline** that:

1.  Crawls multiple predefined sources
2.  Normalizes and deduplicates articles
3.  Stores articles locally as structured JSON
4.  Uses an LLM to classify and score articles
5.  Builds a taxonomy-driven email digest
6.  Sends digest to predefined recipients
7.  Logs failures and alerts on crawl issues

Deployment target: **Render.com Cron Job**

Storage (v1):\
- Temporary local JSON files (ephemeral) - Azure Blob Storage
(persistent layer)

Future: Postgres (not part of v1)

------------------------------------------------------------------------

# 2. Deployment Environment

## 2.1 Runtime

-   Hosted as a **Render.com cron job**
-   Runs 2--3 times per week
-   Example schedule: `0 6 * * 1,3,5` (Mon/Wed/Fri at 06:00 UTC)

## 2.2 Execution Model

Each run is:

-   Fully deterministic
-   Idempotent
-   Logged
-   Based on a sliding time window (e.g., last 3--4 days)

------------------------------------------------------------------------

# 3. Source Configuration

All source definitions are stored in `config.py`.

## 3.1 RSS Feeds

``` python
RSS_FEEDS = [
    "https://example-university.de/rss",
    "https://example-news.de/feed"
]
```

## 3.2 University News Pages (HTML Scraping)

``` python
UNIVERSITY_NEWS_URLS = [
    "https://uni-a.de/news",
    "https://uni-b.de/aktuelles"
]
```

These require: - HTML parsing - Article link extraction - Full article
content extraction

## 3.3 NewsAPI.org

Assumptions: - API key stored in environment variable - Query strings
defined in config

``` python
NEWSAPI_QUERIES = [
    "Universität AND CIO",
    "Hochschule AND Digitalstrategie",
    "Campus Management System rollout",
    "Hochschulgesetz Änderung",
]
```

Query parameters: - language=de - sortBy=publishedAt - from=NOW - 4
days - pageSize=100

------------------------------------------------------------------------

# 4. Directory Structure (Local)

    /tmp/
        raw/
            YYYY-MM-DD/
                rss_*.json
                university_*.json
                newsapi_*.json
        processed/
            YYYY-MM-DD/
                articles_normalized.json
                articles_classified.json
        logs/
            run_YYYY-MM-DD.log

After processing: - Processed files uploaded to Azure Blob Storage -
Local files may be deleted

------------------------------------------------------------------------

# 5. Data Flow

## Step 1 --- Crawl Sources

Sources: - RSS_FEEDS - UNIVERSITY_NEWS_URLS - NewsAPI

Output: Raw JSON per source.

## Step 2 --- Normalize Articles

-   Extract title
-   Extract full raw_text
-   Normalize published_at (ISO 8601)
-   Normalize URL
-   Assign region

## Step 3 --- Generate Article ID

    article_id = sha256(normalized_url + published_at)

## Step 4 --- Deduplication

Based on: - article_id - content_hash

## Step 5 --- LLM Classification

Each non-deduplicated article is classified using strict taxonomy
mapping.

## Step 6 --- Digest Inclusion Rules

Include if:

-   primary_category NOT IN \["Research News", "Irrelevant"\]
-   relevance_score \>= 0.6
-   confidence_score \>= 0.6

Priority bucket:

    >= 0.8 → High
    >= 0.5 → Medium
    else → Low

## Step 7 --- Digest Generation

Sections correspond to primary taxonomy categories.

Articles sorted by: 1. priority_bucket 2. priority_score desc 3.
published_at desc

------------------------------------------------------------------------

# 6. Taxonomy Definition (v1.0)

Allowed Primary Categories:

-   Leadership & Governance
-   Regulatory & Policy Changes
-   Organizational & Structural Changes
-   Digital Strategy & IT Initiatives
-   Funding & Investment Signals
-   Procurement & Tenders
-   Crisis & Risk Events
-   Research News
-   Irrelevant

Primary category: - Exactly one required

Secondary tags: - 0--5 short descriptive labels

------------------------------------------------------------------------

# 7. Canonical Article Schema (v1.0)

``` json
{
  "schema_version": "1.0",
  "article_id": "",
  "source": {
    "type": "newspaper | university_news | blog | rss | api",
    "name": "",
    "url": "",
    "region": "DE | AT | CH | EU | OTHER"
  },
  "content": {
    "title": "",
    "raw_text": "",
    "published_at": "",
    "author": null,
    "language": "de | en"
  },
  "crawl_metadata": {
    "crawl_timestamp": "",
    "content_hash": "",
    "deduplicated": false
  },
  "analysis": {
    "processed": false,
    "taxonomy_version": "1.0",
    "primary_category": null,
    "secondary_tags": [],
    "relevance_score": null,
    "priority_score": null,
    "confidence_score": null,
    "entities": {
      "universities": [],
      "persons": [],
      "roles": [],
      "vendors": [],
      "technologies": [],
      "regions": []
    },
    "signal_summary": null,
    "sales_relevance": null
  },
  "digest": {
    "included": false,
    "digest_id": null,
    "priority_bucket": null
  }
}
```

------------------------------------------------------------------------

# 8. LLM Classification Prompt

## System Prompt

You are a structured information extraction system.

You classify university-related news articles according to a fixed
taxonomy.

You must strictly follow the allowed category list and output valid JSON
only.

Do not include explanations outside JSON.

## User Prompt Template

Assign exactly one primary_category from the allowed list.

Score relevance_score, priority_score, confidence_score (0.0--1.0).

Extract entities and produce concise summaries.

Output STRICT JSON only with the defined structure.

------------------------------------------------------------------------

# 9. Validation Rules

Before saving LLM output:

-   primary_category must match allowed list
-   Scores must be floats between 0--1
-   JSON must parse
-   Required fields must exist

If validation fails: - Retry once - Else log error

------------------------------------------------------------------------

# 10. Failure Monitoring

Track per run:

-   Successful RSS fetches
-   Successful university scrapes
-   NewsAPI status
-   Total article count

If a source fails: - Log error - Send internal alert email

------------------------------------------------------------------------

# 11. Email Digest Structure

Subject:

Simovative HE Market Digest -- {{DATE}}

Body:

Section per taxonomy category.

Each entry:

-   Title
-   University
-   Published date
-   signal_summary
-   sales_relevance
-   Link

Empty sections omitted.

E-mail to go out to bl@flex.capital.

------------------------------------------------------------------------

# 12. Idempotency

Each run has:

    run_id = ISO_TIMESTAMP

Log: - Articles crawled - Deduplicated - Classified - Included -
Errors - Runtime

Logs uploaded to Azure Blob Storage.

------------------------------------------------------------------------

# End of Specification
