"""Per-source transparency report for pipeline runs."""

import logging
from collections import Counter

from src.storage.local_store import processed_dir

logger = logging.getLogger(__name__)


def _count_by_source(articles: list[dict]) -> Counter:
    counts: Counter = Counter()
    for a in articles:
        name = a.get("source", {}).get("name") or "Unknown"
        counts[name] += 1
    return counts


def generate_source_transparency_report(
    normalized: list[dict],
    after_history: list[dict],
    digest: list[dict],
    run_date: str,
) -> str:
    """Build a Markdown source transparency report and write it to the processed dir."""
    retrieved = _count_by_source(normalized)
    history = _count_by_source(after_history)
    in_digest = _count_by_source(digest)

    all_sources = sorted(set(retrieved) | set(history) | set(in_digest))

    lines = [
        f"# Source Transparency Report — {run_date}",
        "",
        "| Source | Retrieved | After History Check | In Digest |",
        "|--------|-----------|---------------------|-----------|",
    ]

    total_r, total_h, total_d = 0, 0, 0
    for src in all_sources:
        r, h, d = retrieved[src], history[src], in_digest[src]
        total_r += r
        total_h += h
        total_d += d
        lines.append(f"| {src} | {r} | {h} | {d} |")

    lines.append(f"| **Total** | **{total_r}** | **{total_h}** | **{total_d}** |")
    lines.append("")

    out = processed_dir(run_date) / f"source_report_{run_date}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Source transparency report written to %s", out)
    return str(out)
