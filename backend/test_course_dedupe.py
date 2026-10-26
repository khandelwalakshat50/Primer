"""One in-flight course per topic — a second plan must not call the planner."""

from unittest.mock import patch

from curriculum import (
    Curriculum,
    ReelSpec,
    course_is_open,
    find_open_curriculum,
    plan_curriculum,
    topic_key,
)


def _open_course(topic: str, curriculum_id: str = "c1") -> Curriculum:
    return Curriculum(
        curriculum_id=curriculum_id,
        topic=topic,
        created_at=1,
        status="generating",
        reels=[
            ReelSpec(
                reel_id="reel_01",
                title="One",
                objective="learn",
                example="ex",
                key_terms=["a"],
                depends_on=[],
                status="pending",
            )
        ],
    )


def test_topic_key_collapses_whitespace_and_case():
    assert topic_key("  Compound  Interest ") == topic_key("compound interest")


def test_course_is_open_while_any_reel_is_pending():
    assert course_is_open(_open_course("x"))
    done = _open_course("x")
    done.status = "ready"
    done.reels[0].status = "ready"
    assert not course_is_open(done)


def test_plan_reuses_open_course_without_calling_planner():
    existing = _open_course("compound interest")

    class Store:
        def list(self):
            return [existing]

        def create(self, _curriculum):
            raise AssertionError("must not create a second course")

    with patch("curriculum.CurriculumStore.default", return_value=Store()):
        with patch("curriculum._plan_once") as planner:
            got = plan_curriculum("Compound  Interest", "", "", "", "")
    assert got is existing
    planner.assert_not_called()


def test_find_open_curriculum_matches_normalized_topic():
    existing = _open_course("Newton's laws")

    class Store:
        def list(self):
            return [existing]

    with patch("curriculum.CurriculumStore.default", return_value=Store()):
        assert find_open_curriculum("newton's   laws") is existing
        assert find_open_curriculum("other topic") is None


if __name__ == "__main__":
    test_topic_key_collapses_whitespace_and_case()
    test_course_is_open_while_any_reel_is_pending()
    test_plan_reuses_open_course_without_calling_planner()
    test_find_open_curriculum_matches_normalized_topic()
    print("ok")
