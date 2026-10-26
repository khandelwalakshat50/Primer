from curriculum import _normalize_plan
from pipeline import _answer_prompt, _director_prior_story, _prior_specs, lesson_position
from curriculum import Curriculum, ReelSpec


def _course() -> Curriculum:
    reels = [
        ReelSpec(
            reel_id="reel_01",
            title="Year one",
            objective="The first year pays ten dollars.",
            example="$100 at 10%",
            key_terms=["interest", "principal"],
            depends_on=[],
            narration_draft="We're going to figure out why sitting money still grows. You leave $100 in a bank at 10%. After a year you have $110. That extra $10 is interest on the original pile.",
        ),
        ReelSpec(
            reel_id="reel_02",
            title="Year two",
            objective="Year two pays more because interest earns interest.",
            example="the same $110 in year two",
            key_terms=["compounding"],
            depends_on=["reel_01"],
            narration_draft="We're still with that $100. Last time it paid $10. Year two starts with $110, so it pays $11. The extra dollar is interest on the interest.",
        ),
        ReelSpec(
            reel_id="reel_03",
            title="The jump",
            objective="Later years pull further ahead.",
            example="year three of the same $100",
            key_terms=["growth"],
            depends_on=["reel_01"],
            narration_draft="",
        ),
    ]
    reels[0].outline = "Object at start: $100. Lands: that extra $10 is interest on the original pile."
    reels[1].outline = "Opens from: that extra $10 is interest. Lands: year two pays $11."
    return Curriculum(
        curriculum_id="t",
        topic="compounding",
        created_at=0,
        reels=reels,
        spine="$100 at 10% for three years",
    )


def test_priors_are_every_earlier_episode():
    course = _course()
    later = course.reels[2]
    prior = _prior_specs(course, later)
    assert [p.reel_id for p in prior] == ["reel_01", "reel_02"]
    assert lesson_position(course, later) == "last"


def test_script_prompt_hands_off_from_previous_episode():
    course = _course()
    spec = course.reels[1]
    prompt = _answer_prompt(course, spec, _prior_specs(course, spec), "", "middle")
    assert "THE VIEWER JUST FINISHED" in prompt
    assert "EPISODE BIBLE" in prompt
    assert "That extra $10 is interest on the original pile." in prompt
    assert "Year one" in prompt


def test_director_gets_previous_script_not_a_snippet():
    course = _course()
    spec = course.reels[1]
    story = _director_prior_story(course, spec, _prior_specs(course, spec))
    assert "episode 2 of 3" in story
    assert "THEY LAST HEARD" in story
    assert "interest on the original pile" in story
    assert "PREVIOUS EPISODE: Year one" in story


def test_syllabus_chains_each_episode_to_the_last():
    plan = _normalize_plan(
        "hft",
        {
            "topic": "hft",
            "spine": "one order in the book",
            "reels": [
                {
                    "reel_id": "reel_01",
                    "title": "Best offer",
                    "objective": "A price is the best offer.",
                    "example": "a buy at 99",
                    "key_terms": ["bid", "ask"],
                    "depends_on": [],
                },
                {
                    "reel_id": "reel_02",
                    "title": "The queue",
                    "objective": "Orders wait in line.",
                    "example": "the same buy waiting",
                    "key_terms": ["queue", "fill"],
                    "depends_on": [],
                },
                {
                    "reel_id": "reel_03",
                    "title": "Speed",
                    "objective": "Being early is worth money.",
                    "example": "the same buy arriving late",
                    "key_terms": ["latency", "queue"],
                    "depends_on": ["reel_01"],
                },
            ],
        },
    )
    assert plan is not None
    assert plan.reels[1].depends_on[0] == "reel_01"
    assert plan.reels[2].depends_on[0] == "reel_02"
    assert "reel_01" in plan.reels[2].depends_on


if __name__ == "__main__":
    test_priors_are_every_earlier_episode()
    test_script_prompt_hands_off_from_previous_episode()
    test_director_gets_previous_script_not_a_snippet()
    test_syllabus_chains_each_episode_to_the_last()
    print("ok")
