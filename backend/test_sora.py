from metrics import video_cost
from sora import clip_seconds, prompt_for_reel


def test_video_cost_sora2_portrait():
    assert video_cost("sora-2", 8, "720x1280") == 0.8
    assert video_cost("sora-2-pro", 4, "720x1280") == 1.2


def test_clip_seconds_from_word_count(monkeypatch):
    monkeypatch.delenv("SORA_SECONDS", raising=False)
    assert clip_seconds(8) == "4"
    assert clip_seconds(20) == "8"
    assert clip_seconds(40) == "12"
    monkeypatch.setenv("SORA_SECONDS", "8")
    assert clip_seconds(8) == "8"


def test_prompt_forbids_on_screen_text():
    prompt = prompt_for_reel("Compounding", "a $100 bill", ["the bill doubles"])
    assert "no text" in prompt.lower()
    assert "captions" in prompt.lower()
    assert "Compounding" in prompt
