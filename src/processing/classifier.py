"""LLM-based article classification using OpenAI."""

import json
import logging
import os

from openai import OpenAI

logger = logging.getLogger(__name__)

ALLOWED_CATEGORIES = [
    "Leadership & Governance",
    "Regulatory & Policy Changes",
    "Organizational & Structural Changes",
    "Digital Strategy & IT Initiatives",
    "Funding & Investment Signals",
    "Procurement & Tenders",
    "Crisis & Risk Events",
    "Research News",
    "Irrelevant",
]

SYSTEM_PROMPT = """\
You are a structured information extraction system for university market intelligence.
You classify German-language news articles according to a fixed taxonomy relevant \
to higher education institutions in the DACH region (Germany, Austria, Switzerland).
If the article concerns a university outside the DACH region, classify it as "Irrelevant". If the article only talks about research budgets, classify it as "Research News".
You must strictly follow the allowed category list and output valid JSON only.
Do not include any text outside the JSON object."""

USER_PROMPT_TEMPLATE = """\
Classify the following German university-sector news article.

Title: {title}

Text: {text}

Allowed primary categories: {categories}

Output a JSON object with exactly these fields:
- "primary_category": exactly one of the allowed categories above
- "secondary_tags": list of 0-5 short descriptive labels (strings)
- "relevance_score": float 0.0-1.0 (how relevant to university market intelligence)
- "priority_score": float 0.0-1.0 (urgency/importance for sales intelligence)
- "confidence_score": float 0.0-1.0 (your confidence in this classification)
- "entities": object with keys "universities", "persons", "roles", "vendors", \
"technologies", "regions" — each a list of strings
- "signal_summary": 1-2 sentence German summary of the key signal
- "sales_relevance": 1 sentence explaining sales relevance for a university \
ERP/campus management vendor

Output STRICT JSON only."""


def _validate_llm_output(data: dict) -> bool:
    if data.get("primary_category") not in ALLOWED_CATEGORIES:
        return False
    for score_field in ("relevance_score", "priority_score", "confidence_score"):
        val = data.get(score_field)
        if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 1.0):
            return False
    if not isinstance(data.get("entities"), dict):
        return False
    return True


def _get_client() -> OpenAI:
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def classify_article(article: dict, client: OpenAI, model: str) -> dict:
    """Classify a single article using the LLM. Retries once on failure."""
    title = article["content"]["title"]
    raw_text = article["content"]["raw_text"] or ""
    text_snippet = raw_text[:3000]

    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": USER_PROMPT_TEMPLATE.format(
                        title=title,
                        text=text_snippet,
                        categories=", ".join(ALLOWED_CATEGORIES),
                    )},
                ],
                temperature=0.1,
            )
            raw_json = response.choices[0].message.content
            data = json.loads(raw_json)

            if _validate_llm_output(data):
                article["analysis"].update({
                    "processed": True,
                    "primary_category": data["primary_category"],
                    "secondary_tags": data.get("secondary_tags", [])[:5],
                    "relevance_score": float(data["relevance_score"]),
                    "priority_score": float(data["priority_score"]),
                    "confidence_score": float(data["confidence_score"]),
                    "entities": data.get("entities", {}),
                    "signal_summary": data.get("signal_summary"),
                    "sales_relevance": data.get("sales_relevance"),
                })
                return article
            else:
                logger.warning("Validation failed on attempt %d for %s", attempt + 1, article["article_id"])

        except Exception as e:
            logger.warning("LLM classification attempt %d failed for %s: %s",
                           attempt + 1, article["article_id"], e)

    logger.error("Classification failed after 2 attempts for article: %s", article["article_id"])
    return article


def classify_articles(articles: list[dict]) -> list[dict]:
    """Classify all articles using OpenAI. Skips articles with no title."""
    model = os.getenv("OPENAI_MODEL_NAME", "gpt-4.1")
    client = _get_client()

    classified = []
    for i, article in enumerate(articles, 1):
        title = article["content"]["title"]
        if not title:
            logger.info("Skipping article %s — no title", article["article_id"])
            classified.append(article)
            continue

        logger.info("Classifying %d/%d: %s", i, len(articles), title[:80])
        classified.append(classify_article(article, client, model))

    processed = sum(1 for a in classified if a["analysis"]["processed"])
    logger.info("Classification complete: %d/%d articles processed", processed, len(classified))
    return classified
