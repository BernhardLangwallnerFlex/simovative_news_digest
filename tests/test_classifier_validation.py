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
