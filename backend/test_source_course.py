from curriculum import (
    SOURCE_CHAR_LIMIT,
    _normalize_plan,
    clip_source,
    topic_key,
)


def test_clip_source_keeps_short_text():
    assert clip_source("hello") == "hello"


def test_clip_source_truncates():
    blob = "a" * (SOURCE_CHAR_LIMIT + 80)
    out = clip_source(blob)
    assert len(out) <= SOURCE_CHAR_LIMIT
    assert "[source truncated]" in out


def test_topic_key_source_ignores_title():
    assert topic_key("one title", "Same document") == topic_key("other title", "Same document")
    assert topic_key("t", "Document A") != topic_key("t", "Document B")


def _reels(count: int) -> list[dict]:
    return [
        {
            "reel_id": f"reel_{i:02d}",
            "title": f"Title {i}",
            "objective": f"Learn idea {i} clearly",
            "example": f"The same $100 in year {i}",
            "key_terms": ["alpha", "beta"],
            "depends_on": [],
            "difficulty": i / 10,
        }
        for i in range(1, count + 1)
    ]


def test_source_plan_uses_json_title_and_keeps_source():
    source = "A long pasted article about bank runs and deposit insurance.\n" * 8
    plan = _normalize_plan(
        "ignored",
        {"topic": "Bank Runs", "spine": "one bank's window", "reels": _reels(4)},
        source,
    )
    assert plan is not None
    assert plan.topic == "Bank Runs"
    assert plan.source_text.startswith("A long pasted article")
    assert plan.dump(include_source=False).get("source_text") is None


def test_topic_plan_rejects_too_few_reels():
    assert _normalize_plan("rates", {"reels": _reels(2)}) is None


if __name__ == "__main__":
    test_clip_source_keeps_short_text()
    test_clip_source_truncates()
    test_topic_key_source_ignores_title()
    test_source_plan_uses_json_title_and_keeps_source()
    test_topic_plan_rejects_too_few_reels()
    print("ok")
