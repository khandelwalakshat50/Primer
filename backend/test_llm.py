from llm import normalize_provider, order, prefer, preferred


def test_provider_order():
    prefer("auto")
    assert order("auto") == ["openai", "gemini", "claude"]
    assert order("claude") == ["claude"]
    assert order("anthropic") == ["claude"]
    assert order("openai") == ["openai"]
    assert normalize_provider("sonnet") == "claude"
    assert normalize_provider("opus") == "claude"


def test_prefer_is_visible_without_contextvar():
    prefer("claude")
    assert preferred() == "claude"
    assert order() == ["claude"]


if __name__ == "__main__":
    test_provider_order()
    test_prefer_is_visible_without_contextvar()
    print("ok")
