from curriculum import Curriculum, ReelSpec
from pipeline import _prior_episode_settled


def _course(*statuses: str) -> Curriculum:
    reels = [
        ReelSpec(
            reel_id=f"reel_{i:02d}",
            title=str(i),
            objective="learn",
            example="ex",
            key_terms=["a"],
            depends_on=[] if i == 1 else [f"reel_{i-1:02d}"],
            status=status,
        )
        for i, status in enumerate(statuses, start=1)
    ]
    return Curriculum(
        curriculum_id="q",
        topic="interest",
        created_at=1,
        status="generating",
        reels=reels,
    )


def test_first_episode_can_always_direct():
    cur = _course("scripted", "scripted")
    assert _prior_episode_settled(cur, cur.reels[0]) is True


def test_later_episode_waits_for_previous_ready():
    cur = _course("generating", "scripted")
    assert _prior_episode_settled(cur, cur.reels[1]) is False
    cur.reels[0].status = "ready"
    assert _prior_episode_settled(cur, cur.reels[1]) is True
    cur.reels[0].status = "failed"
    assert _prior_episode_settled(cur, cur.reels[1]) is True


if __name__ == "__main__":
    test_first_episode_can_always_direct()
    test_later_episode_waits_for_previous_ready()
    print("ok")
