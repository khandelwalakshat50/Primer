import tempfile
from pathlib import Path

from curriculum import Curriculum, CurriculumStore, ReelSpec, course_is_open


def _course(status: str = "generating") -> Curriculum:
    return Curriculum(
        curriculum_id="stop-me",
        topic="interest",
        created_at=1,
        status=status,
        reels=[
            ReelSpec(
                reel_id="reel_01",
                title="One",
                objective="learn",
                example="ex",
                key_terms=["a"],
                depends_on=[],
                status="ready",
            ),
            ReelSpec(
                reel_id="reel_02",
                title="Two",
                objective="learn more",
                example="ex",
                key_terms=["b"],
                depends_on=["reel_01"],
                status="pending",
            ),
        ],
    )


def test_fail_unfinished_keeps_ready_lessons():
    root = Path(tempfile.mkdtemp())
    store = CurriculumStore(root)
    store.create(_course())
    out = store.fail_unfinished("stop-me", error="Stopped")
    assert out is not None
    assert out.reels[0].status == "ready"
    assert out.reels[1].status == "failed"
    assert out.reels[1].error == "Stopped"
    assert out.status == "ready"
    assert not course_is_open(out)


if __name__ == "__main__":
    test_fail_unfinished_keeps_ready_lessons()
    print("ok")
