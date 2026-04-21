#!/usr/bin/env python3
"""Simovative University Market Intelligence News Digest — Pipeline Entry Point."""

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

from dotenv import load_dotenv

load_dotenv()

from config import RSS_FEEDS, MANDATORY_DOMAINS, ARTICLES_PER_DOMAIN, NEWSAPI_QUERIES, UNIVERSITY_NEWS_URLS, EMAIL_RECIPIENTS, LINK_CLASSIFIER_ENABLED, LINK_DISCOVERY_MODEL, AZURE_HISTORY_CONTAINER, AZURE_HISTORY_BLOB
from src.crawlers.university_domain_crawler import crawl_all_university_domains
from src.delivery.email_sender import send_digest_email
from src.crawlers.newsapi_crawler import fetch_newsapi
from src.crawlers.rss_crawler import parse_rss_feeds
from src.digest.html_generator import filter_for_digest, generate_html_digest
from src.processing.classifier import classify_articles
from src.processing.deduplicator import deduplicate
from src.processing.near_dedup import deduplicate_near_duplicates
from src.processing.history import load_history, filter_new_articles, update_history, save_history
from src.processing.normalizer import normalize_articles
from src.storage.local_store import (
    digest_path,
    get_run_date,
    save_processed,
    save_raw,
)
from src.reporting.source_transparency import generate_source_transparency_report
from src.utils.logging_setup import setup_logging

DAYS_BACK = 14


def main():
    run_date = get_run_date()
    run_id = datetime.utcnow().isoformat() + "Z"
    logger = setup_logging(run_date)
    logger.info("=== Run started | run_id=%s | date=%s ===", run_id, run_date)

    # Warm up link classifier model (loads once, reused across all domains)
    if LINK_CLASSIFIER_ENABLED:
        from src.crawlers.link_classifier import LinkClassifier
        LinkClassifier.get_instance(model_name=LINK_DISCOVERY_MODEL)
        logger.info("Link classifier model loaded: %s", LINK_DISCOVERY_MODEL)

    end_date = run_date
    start_date = (datetime.strptime(run_date, "%Y-%m-%d") - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
    logger.info("Time window: %s to %s", start_date, end_date)

    stats = {
        "rss_articles": 0,
        "domain_articles": 0,
        "newsapi_articles": 0,
        "normalized": 0,
        "after_dedup": 0,
        "after_history_filter": 0,
        "history_skipped": 0,
        "classified": 0,
        "digest_included": 0,
        "errors": [],
    }

    # ── Step 1: Crawl all sources concurrently ─────────────────────────
    logger.info(
        "Step 1: Crawling all sources concurrently "
        "(RSS=%d, university=%d, mandatory=%d, NewsAPI=%d queries)",
        len(RSS_FEEDS), len(UNIVERSITY_NEWS_URLS), len(MANDATORY_DOMAINS), len(NEWSAPI_QUERIES),
    )

    def _crawl_rss():
        result = parse_rss_feeds(RSS_FEEDS, start_date, end_date)
        save_raw(result, "rss_articles", run_date)
        return result

    def _crawl_domains():
        all_domains = UNIVERSITY_NEWS_URLS + MANDATORY_DOMAINS
        result = crawl_all_university_domains(
            all_domains, max_articles=ARTICLES_PER_DOMAIN, days_back=DAYS_BACK
        )
        save_raw(result, "domain_articles", run_date)
        return result

    def _crawl_newsapi():
        result = fetch_newsapi(NEWSAPI_QUERIES, days_back=DAYS_BACK)
        save_raw(result, "newsapi_articles", run_date)
        return result

    rss_raw, domain_raw, newsapi_raw = [], [], []

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_rss = executor.submit(_crawl_rss)
        future_domains = executor.submit(_crawl_domains)
        future_newsapi = executor.submit(_crawl_newsapi)

        for name, future, target in [
            ("RSS", future_rss, "rss_raw"),
            ("Domains", future_domains, "domain_raw"),
            ("NewsAPI", future_newsapi, "newsapi_raw"),
        ]:
            try:
                result = future.result()
                if target == "rss_raw":
                    rss_raw = result
                elif target == "domain_raw":
                    domain_raw = result
                else:
                    newsapi_raw = result
                logger.info("%s: %d articles collected", name, len(result))
            except Exception as e:
                logger.error("%s crawl failed: %s", name, e)
                stats["errors"].append(f"{name}: {e}")

    stats["rss_articles"] = len(rss_raw)
    stats["domain_articles"] = len(domain_raw)
    stats["newsapi_articles"] = len(newsapi_raw)

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

    # ── Step 4.5: History Filter ─────────────────────────────────────
    logger.info("Step 4.5: Loading article history from Azure")
    history = load_history(AZURE_HISTORY_CONTAINER, AZURE_HISTORY_BLOB)
    new_articles, history_skipped = filter_new_articles(unique, history)
    stats["after_history_filter"] = len(new_articles)
    stats["history_skipped"] = history_skipped
    logger.info(
        "History filter: %d new articles to classify, %d already seen",
        len(new_articles), history_skipped,
    )

    # ── Step 5: LLM Classification ───────────────────────────────────
    logger.info("Step 5: Classifying %d articles", len(new_articles))
    classified = classify_articles(new_articles)
    stats["classified"] = sum(1 for a in classified if a["analysis"]["processed"])
    save_processed(classified, "articles_classified.json", run_date)
    logger.info("Classified: %d / %d articles", stats["classified"], len(new_articles))

    # ── Step 5.5: Update and persist history ─────────────────────────
    logger.info("Step 5.5: Updating article history")
    history = update_history(history, new_articles, run_date)
    save_history(history, AZURE_HISTORY_CONTAINER, AZURE_HISTORY_BLOB)

    # ── Step 6+7: Digest Generation ──────────────────────────────────
    logger.info("Step 6+7: Generating digest")
    digest_articles = filter_for_digest(classified)

    # -- Step 6.5: Near-duplicate detection --
    logger.info("Step 6.5: Near-duplicate detection (%d articles)", len(digest_articles))
    digest_articles = deduplicate_near_duplicates(digest_articles)

    stats["digest_included"] = len(digest_articles)

    # ── Source Transparency Report ──────────────────────────────────
    generate_source_transparency_report(normalized, new_articles, digest_articles, run_date)

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
