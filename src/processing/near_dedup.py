"""LLM-based near-duplicate detection for agency wire stories."""

import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You identify near-duplicate news articles — stories that report the same event \
or announcement but were published by different outlets (e.g. agency wire stories \
picked up by multiple newspapers).
You receive a numbered list of article titles and summaries. Return groups of \
duplicates as JSON.
Two articles are near-duplicates only if they report on the exact same specific \
event or announcement, not merely the same general topic."""

USER_PROMPT_TEMPLATE = """\
Below are {count} articles. Identify groups of articles that cover the SAME specific \
event or announcement (near-duplicates / agency wire stories).

Articles:
{article_list}

Output a JSON object with a single key "duplicate_groups". The value is a list of \
lists of article numbers (1-based). Only include groups with 2 or more articles. \
If no duplicates exist, return {{"duplicate_groups": []}}.

Example: {{"duplicate_groups": [[1, 5, 12], [3, 7]]}}"""


def _validate_groups(data: dict, article_count: int) -> list[list[int]]:
    """Validate and sanitize the LLM response. Returns 0-based index groups."""
    groups = data.get("duplicate_groups", [])
    if not isinstance(groups, list):
        return []
    validated = []
    for group in groups:
        if not isinstance(group, list) or len(group) < 2:
            continue
        indices = [i - 1 for i in group if isinstance(i, int) and 1 <= i <= article_count]
        if len(indices) >= 2:
            validated.append(indices)
    return validated


def _merge_group(articles: list[dict], indices: list[int]) -> int:
    """Merge a group of duplicate articles. Returns index of primary article."""
    group = [(i, articles[i]) for i in indices]
    group.sort(key=lambda x: (
        -(x[1]["analysis"].get("priority_score") or 0),
        -len(x[1]["content"].get("raw_text") or ""),
    ))
    primary_idx = group[0][0]
    primary = articles[primary_idx]

    also_reported = primary.setdefault("digest", {}).setdefault("also_reported_by", [])
    for idx, art in group[1:]:
        also_reported.append({
            "name": art["source"]["name"],
            "url": art["source"]["url"],
        })

    return primary_idx


def deduplicate_near_duplicates(articles: list[dict]) -> list[dict]:
    """Detect near-duplicate articles via LLM and merge them into groups.

    On failure, returns the original list unchanged.
    """
    if len(articles) < 2:
        return articles

    if len(articles) > 150:
        logger.warning("Too many articles (%d) for near-dedup, skipping", len(articles))
        return articles

    lines = []
    for i, a in enumerate(articles, 1):
        title = a["content"].get("title") or ""
        summary = a["analysis"].get("signal_summary") or ""
        lines.append(f'{i}. Title: "{title}" | Summary: "{summary}"')

    user_prompt = USER_PROMPT_TEMPLATE.format(
        count=len(articles),
        article_list="\n".join(lines),
    )

    try:
        client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        response = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL_NAME", "gpt-4.1"),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0,
        )
        data = json.loads(response.choices[0].message.content)
        groups = _validate_groups(data, len(articles))
    except Exception as e:
        logger.warning("Near-duplicate detection failed, skipping: %s", e)
        return articles

    if not groups:
        logger.info("Near-dedup: no duplicate groups found")
        return articles

    removed_indices = set()
    claimed = set()
    for group_indices in groups:
        group_indices = [i for i in group_indices if i not in claimed]
        if len(group_indices) < 2:
            continue
        primary_idx = _merge_group(articles, group_indices)
        for idx in group_indices:
            if idx != primary_idx:
                removed_indices.add(idx)
        claimed.update(group_indices)

    result = [a for i, a in enumerate(articles) if i not in removed_indices]
    logger.info(
        "Near-dedup: %d groups found, %d articles merged, %d remain",
        len(groups), len(removed_indices), len(result),
    )
    return result
