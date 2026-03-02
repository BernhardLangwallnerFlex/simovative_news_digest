#!/usr/bin/env python3
"""Simovative University Market Intelligence News Digest — Pipeline Entry Point."""

import sys
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from config import RSS_FEEDS, MANDATORY_DOMAINS, ARTICLES_PER_DOMAIN, NEWSAPI_QUERIES, UNIVERSITY_NEWS_URLS, EMAIL_RECIPIENTS
from src.crawlers.domain_crawler import crawl_domain
from src.delivery.email_sender import send_digest_email
from src.crawlers.newsapi_crawler import fetch_newsapi
from src.crawlers.rss_crawler import parse_rss_feeds
from src.digest.html_generator import filter_for_digest, generate_html_digest
from src.processing.classifier import classify_articles
from src.processing.deduplicator import deduplicate
from src.processing.normalizer import normalize_articles
from src.storage.local_store import (
    digest_path,
    get_run_date,
    save_processed,
    save_raw,
)
from src.utils.logging_setup import setup_logging

DAYS_BACK = 4


def main():
    run_date = get_run_date()
    run_id = datetime.utcnow().isoformat() + "Z"
    logger = setup_logging(run_date)
    logger.info("=== Run started | run_id=%s | date=%s ===", run_id, run_date)

    end_date = run_date
    start_date = (datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    logger.info("Time window: %s to %s", start_date, end_date)

    stats = {
        "rss_articles": 0,
        "domain_articles": 0,
        "newsapi_articles": 0,
        "normalized": 0,
        "after_dedup": 0,
        "classified": 0,
        "digest_included": 0,
        "errors": [],
    }

    # ── Step 1a: RSS Feeds ───────────────────────────────────────────
    logger.info("Step 1a: Crawling %d RSS feeds", len(RSS_FEEDS))
    try:
        rss_raw = parse_rss_feeds(RSS_FEEDS, start_date, end_date)
        stats["rss_articles"] = len(rss_raw)
        save_raw(rss_raw, "rss_articles", run_date)
        logger.info("RSS: %d articles collected", len(rss_raw))
    except Exception as e:
        logger.error("RSS crawl failed: %s", e)
        stats["errors"].append(f"RSS: {e}")
        rss_raw = []

     # ── Step 1b: Domain Scraping (HTML) ─────────────────────────────
    logger.info("Step 1b: Crawling %d university domains", len(MANDATORY_DOMAINS))
    domain_raw = []
    for domain_url in UNIVERSITY_NEWS_URLS:
        try:
            articles = crawl_domain(domain_url, max_articles=ARTICLES_PER_DOMAIN, days_back=DAYS_BACK)
            domain_raw.extend(articles)
            logger.info("Domain %s: %d articles", domain_url, len(articles))
        except Exception as e:
            logger.error("Domain crawl failed for %s: %s", domain_url, e)
            stats["errors"].append(f"Domain {domain_url}: {e}")
    stats["domain_articles"] = len(domain_raw)
    save_raw(domain_raw, "university_articles", run_date)
    for domain_url in MANDATORY_DOMAINS:
        try:
            articles = crawl_domain(domain_url, max_articles=ARTICLES_PER_DOMAIN, days_back=DAYS_BACK)
            domain_raw.extend(articles)
            logger.info("Domain %s: %d articles", domain_url, len(articles))
        except Exception as e:
            logger.error("Domain crawl failed for %s: %s", domain_url, e)
            stats["errors"].append(f"Domain {domain_url}: {e}")
    stats["domain_articles"] = len(domain_raw)
    save_raw(domain_raw, "university_articles", run_date) 

    # ── Step 1c: NewsAPI ─────────────────────────────────────────────
    logger.info("Step 1c: Fetching NewsAPI (%d queries)", len(NEWSAPI_QUERIES))
    try:
        newsapi_raw = fetch_newsapi(NEWSAPI_QUERIES, days_back=DAYS_BACK)
        stats["newsapi_articles"] = len(newsapi_raw)
        save_raw(newsapi_raw, "newsapi_articles", run_date)
        logger.info("NewsAPI: %d articles collected", len(newsapi_raw))
    except Exception as e:
        logger.error("NewsAPI failed: %s", e)
        stats["errors"].append(f"NewsAPI: {e}")
        newsapi_raw = []

    # ── Step 2: Normalize ────────────────────────────────────────────
    logger.info("Step 2: Normalizing articles")
    all_raw = rss_raw + domain_raw + newsapi_raw
    normalized = normalize_articles(all_raw)
    stats["normalized"] = len(normalized)
    save_processed(normalized, "articles_normalized.json", run_date)
    logger.info("Normalized: %d articles", len(normalized))

    # ── Step 3+4: Deduplication ──────────────────────────────────────
    logger.info("Step 3+4: Deduplicating")
    unique = deduplicate(normalized)
    stats["after_dedup"] = len(unique)
    logger.info("After dedup: %d unique (removed %d)",
                len(unique), len(normalized) - len(unique))

    # ── Step 5: LLM Classification ───────────────────────────────────
    logger.info("Step 5: Classifying %d articles", len(unique))
    classified = classify_articles(unique)
    stats["classified"] = sum(1 for a in classified if a["analysis"]["processed"])
    save_processed(classified, "articles_classified.json", run_date)
    logger.info("Classified: %d / %d articles", stats["classified"], len(unique))

    # ── Step 6+7: Digest Generation ──────────────────────────────────
    logger.info("Step 6+7: Generating digest")
    digest_articles = filter_for_digest(classified)
    stats["digest_included"] = len(digest_articles)

    html = generate_html_digest(digest_articles, run_date=run_date)
    out_path = digest_path(run_date)
    out_path.write_text(html, encoding="utf-8")
    logger.info("Digest written to %s (%d articles)", out_path, len(digest_articles))

    # ── Step 8: Send Digest Email ─────────────────────────────────────
    if EMAIL_RECIPIENTS:
        logger.info("Step 8: Sending digest email to %d recipients", len(EMAIL_RECIPIENTS))
        try:
            send_digest_email(
                html=html,
                subject=f"News Digest {run_date}",
                recipients=EMAIL_RECIPIENTS,
            )
        except Exception as e:
            logger.error("Email sending failed: %s", e)
            stats["errors"].append(f"Email: {e}")
    else:
        logger.info("Step 8: Skipped — no EMAIL_RECIPIENTS configured")

    # ── Summary ──────────────────────────────────────────────────────
    logger.info("=== Run complete | run_id=%s ===", run_id)
    logger.info("Stats: %s", stats)

    if stats["errors"]:
        logger.warning("Errors during run: %s", stats["errors"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
