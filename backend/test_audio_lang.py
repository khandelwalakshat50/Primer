from audio_cache import cache_key, cached_hindi_path, english_cache_model, normalize


def test_normalize_keeps_english_narration():
    assert "paycheck" in normalize("Your paycheck lands. Then tax takes a cut.").lower()


def test_cache_key_differs_by_model():
    text = "Hello there."
    assert cache_key(text, "aura-2-athena-en") != cache_key(text, "elevenlabs:x:y:hi")
    assert "en:bright" in english_cache_model()


def test_hindi_cache_miss():
    assert cached_hindi_path("a line that has never been translated") is None


if __name__ == "__main__":
    test_normalize_keeps_english_narration()
    test_cache_key_differs_by_model()
    test_hindi_cache_miss()
    print("ok")
