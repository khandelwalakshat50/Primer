from storyboard import OverlayLine, StoryBeat, Storyboard, VisualPlan, VisualWorld, WorldObject, assign_mixed_media, fill_photo_overlays
from metrics import image_cost
from sora import prompt_for_beat
from media_safety import abstract_object_shot, is_moderation_error


def _board(types: list[str]) -> Storyboard:
    roles = ["hook", "establish", "transform", "revelation", "payoff", "build"]
    beats = []
    for i, kind in enumerate(types):
        beats.append(
            StoryBeat(
                role=roles[i] if i < len(roles) else "build",
                heading=f"Beat {i} cash",
                narration="Cash stacks grow when interest is added.",
                visual=VisualPlan(
                    type=kind,
                    sees="a stack of bills",
                    keeps="the stack" if i else "",
                    changes="a new bill" if i else "-",
                    motion="grows upward" if kind == "accumulation" else "",
                    emphasis="loud" if i == 2 else "normal",
                ),
            )
        )
    return Storyboard(
        title="Cash stack",
        world=VisualWorld(
            objects=[WorldObject(id="stack", what="bills", how_drawn="rectangles")],
            anchor="center",
            accent_means="new interest",
        ),
        beats=beats,
    )


def test_assign_mixed_media_budget():
    board = _board(["typography", "object", "accumulation", "graph", "comparison", "minimal"])
    assign_mixed_media(board)
    media = [b.visual.medium for b in board.beats]
    assert media.count("sora") == 1
    assert media.count("image") == 2
    assert media.count("svg") == 3
    assert board.beats[0].visual.medium == "sora"
    assert board.beats[3].visual.medium == "svg"
    assert board.beats[4].visual.medium == "svg"


def test_sora_is_always_first():
    board = _board(["object", "object", "graph", "typography", "minimal"])
    board.beats[0].visual.medium = "image"
    board.beats[1].visual.medium = "sora"
    board.beats[4].visual.medium = "image"
    assign_mixed_media(board)
    assert board.beats[0].visual.medium == "sora"
    assert [b.visual.medium for b in board.beats].count("image") == 2
    assert [b.visual.medium for b in board.beats].count("sora") == 1


def test_photo_overlays_placed():
    beat = StoryBeat(
        heading="The stack",
        narration="Interest is added on top of the stack.",
        visual=VisualPlan(type="object", sees="bills", focus="the new bill", changes="one bill is added"),
        overlays=[OverlayLine(text="The stack", place="top", emphasis="high")],
    )
    fill_photo_overlays(beat)
    assert len(beat.overlays) >= 3
    places = [o.place for o in beat.overlays]
    assert len(set(places)) == len(places)


def test_image_cost_gpt_image():
    assert image_cost("gpt-image-1", "1024x1536") == 0.063


def test_beat_prompt_forbids_text():
    prompt = prompt_for_beat("Compounding", "a $100 bill", "the bill doubles")
    assert "no text" in prompt.lower()
    assert "ONE beat" in prompt
    assert "the bill doubles" in prompt
    assert "no public figures" in prompt.lower()


def test_moderation_retry_shot_has_no_people():
    shot = abstract_object_shot("Nehru and Gandhi walk through a crowd of refugees")
    assert "nehru" not in shot.lower()
    assert "gandhi" not in shot.lower()
    assert "crowd" not in shot.lower()
    assert "No humans" in shot
    assert is_moderation_error(RuntimeError("Sora video failed: blocked by our moderation system"))


if __name__ == "__main__":
    test_assign_mixed_media_budget()
    test_sora_is_always_first()
    test_photo_overlays_placed()
    test_image_cost_gpt_image()
    test_beat_prompt_forbids_text()
    test_moderation_retry_shot_has_no_people()
    print("ok")
