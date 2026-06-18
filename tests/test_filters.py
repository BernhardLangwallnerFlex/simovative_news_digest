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
