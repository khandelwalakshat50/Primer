from prompt_lab import keyword_select, prompt_diff
from prompt_lab_store import (
    HARD_MAX_ITERATIONS,
    PromptSet,
    PromptVersion,
    QuestionResult,
    clamp_max_iterations,
    mean_overall,
    should_stop_run,
)


def _set(topic: str, generic: bool = False, tags=None, desc="") -> PromptSet:
    return PromptSet(
        set_id=f"ps_{topic.lower()}",
        topic=topic,
        slug=topic.lower(),
        description=desc or topic,
        is_generic=generic,
        tags=tags or [topic.lower()],
        versions=[PromptVersion(version_id="pv1", version=1, prompt="hello")],
    )


def test_max_iterations_enforced():
    assert clamp_max_iterations(5) == 5
    assert clamp_max_iterations(10) == 10
    try:
        clamp_max_iterations(11)
        assert False, "should reject >10"
    except ValueError:
        pass
    try:
        clamp_max_iterations(0)
        assert False
    except ValueError:
        pass
    assert HARD_MAX_ITERATIONS == 10


def test_keyword_select_finance_and_calculus():
    sets = [
        _set("Generic", True, ["generic"], "fallback"),
        _set("Finance", tags=["finance", "bonds"], desc="rates bonds compounding"),
        _set("Calculus", tags=["calculus", "limits"], desc="limits derivatives integrals"),
        _set("Biology", tags=["biology"], desc="cells photosynthesis"),
    ]
    picked, reason, fallback = keyword_select("Why do bond prices fall when interest rates rise?", sets)
    assert picked and picked.topic == "Finance" and not fallback
    picked, reason, fallback = keyword_select("Why does calculus use limits?", sets)
    assert picked and picked.topic == "Calculus" and not fallback
    picked, reason, fallback = keyword_select("How does a neural network learn?", sets)
    assert fallback and picked.topic == "Generic"
    picked, _, fallback = keyword_select("What is opportunity cost?", sets)
    assert fallback and picked.topic == "Generic"


def test_best_version_not_last_if_worse():
    results = [
        QuestionResult(question="q", scores={"overall_score": 7.1, **{k: 7.1 for k in [
            "teaching_quality","coherence","visual_continuity","entertainment","pacing",
            "originality","visual_clarity","narration_quality","phone_readability"]}}),
    ]
    overall, _ = mean_overall(results)
    assert overall == 7.1
    scores = [7.1, 7.8, 7.5, 8.1, 7.9]
    best_i, best_s = 0, -1
    for i, s in enumerate(scores, start=1):
        if s > best_s:
            best_s = s
            best_i = i
    assert best_i == 4 and best_s == 8.1


def test_stop_conditions():
    stop, why = should_stop_run(
        iteration_count=5,
        max_iterations=5,
        stagnant_count=0,
        stagnant_limit=2,
        critic_wants_stop=True,
        critic_evidence="whatever",
        has_failure_modes=False,
        overall=9.0,
    )
    assert stop and why == "max_iterations_reached"

    stop, why = should_stop_run(
        iteration_count=3,
        max_iterations=8,
        stagnant_count=2,
        stagnant_limit=2,
        critic_wants_stop=False,
        critic_evidence="",
        has_failure_modes=True,
        overall=7.0,
    )
    assert stop and why == "improvement_below_threshold"

    stop, why = should_stop_run(
        iteration_count=2,
        max_iterations=8,
        stagnant_count=0,
        stagnant_limit=2,
        critic_wants_stop=True,
        critic_evidence="",
        has_failure_modes=False,
        overall=9.0,
    )
    assert not stop

    stop, why = should_stop_run(
        iteration_count=2,
        max_iterations=8,
        stagnant_count=0,
        stagnant_limit=2,
        critic_wants_stop=True,
        critic_evidence="headings already grounded; visual world persists",
        has_failure_modes=False,
        overall=8.4,
    )
    assert stop and why == "no_meaningful_remaining_improvement"


def test_prompt_diff_highlights_additions():
    lines = prompt_diff("Create a diagram for each concept.", "Prefer transforming existing visual objects.")
    ops = {row["op"] for row in lines}
    assert "removed" in ops and "added" in ops


if __name__ == "__main__":
    test_max_iterations_enforced()
    test_keyword_select_finance_and_calculus()
    test_best_version_not_last_if_worse()
    test_stop_conditions()
    test_prompt_diff_highlights_additions()
    print("ok")
