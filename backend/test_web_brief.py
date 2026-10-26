from web_brief import attach, needs_live_web


def test_needs_live_web():
    assert needs_live_web("What's the latest news on the Fed rate decision?")
    assert needs_live_web("Who won the election today?")
    assert not needs_live_web("Why does compound interest grow faster in later years?")


def test_attach_skips_empty():
    assert attach("hello", "") == "hello"
    out = attach("hello", "Fed held rates.")
    assert "LIVE WEB BRIEFING" in out
    assert "Fed held rates." in out


if __name__ == "__main__":
    test_needs_live_web()
    test_attach_skips_empty()
    print("ok")
