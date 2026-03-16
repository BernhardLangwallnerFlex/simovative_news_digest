"""Cross-run article history: load from Azure, filter new articles, save updated history."""

import logging

from src.storage.blob_store import download_json, upload_json

logger = logging.getLogger(__name__)


def load_history(container: str, blob_name: str) -> dict:
    """Load article history from Azure Blob Storage.

    Returns an empty dict on first run or if Azure is unavailable.
    """
    try:
        data = download_json(container, blob_name)
        if data is None:
            logger.info("History: no existing blob found — starting fresh")
            return {}
        if not isinstance(data, dict):
            logger.warning(
                "History: unexpected format (expected dict, got %s) — starting fresh",
                type(data).__name__,
            )
            return {}
        logger.info("History: loaded %d previously seen article IDs", len(data))
        return data
    except Exception as e:
        logger.warning(
            "History: could not load from Azure (%s) — proceeding without history filtering",
            e,
        )
        return {}


def filter_new_articles(articles: list[dict], history: dict) -> tuple[list[dict], int]:
    """Remove articles whose article_id already appears in history.

    Returns (new_articles, skipped_count).
    """
    new_articles = []
    skipped = 0
    for article in articles:
        aid = article["article_id"]
        if aid in history:
            skipped += 1
        else:
            new_articles.append(article)
    logger.info(
        "History filter: %d new, %d already seen (skipped)",
        len(new_articles),
        skipped,
    )
    return new_articles, skipped


def update_history(history: dict, articles: list[dict], run_date: str) -> dict:
    """Add newly seen articles to the history dict.

    Existing entries are not overwritten (first_seen date is preserved).
    """
    added = 0
    for article in articles:
        aid = article["article_id"]
        if aid not in history:
            history[aid] = {
                "url": article["source"]["url"],
                "first_seen": run_date,
            }
            added += 1
    logger.info("History: added %d new article IDs (total: %d)", added, len(history))
    return history


def save_history(history: dict, container: str, blob_name: str) -> None:
    """Persist the updated history back to Azure Blob Storage."""
    try:
        upload_json(container, blob_name, history)
        logger.info("History: saved %d article IDs to Azure", len(history))
    except Exception as e:
        logger.warning(
            "History: could not save to Azure (%s) — history NOT updated for this run",
            e,
        )
