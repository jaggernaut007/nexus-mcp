"""Tests for benchmarks.scoring: per-run metrics against ground truth."""


from benchmarks.scoring import (
    fact_score,
    file_recall,
    judge_prompt,
    mechanical_score,
    normalize_path,
    parse_judge_output,
    score_run,
    wasted_read_ratio,
)


class TestNormalizePath:
    def test_normalize_path_strips_repo_root(self):
        assert normalize_path("/repo/django/x.py", repo_root="/repo") == "django/x.py"

    def test_normalize_path_leaves_relative_path_untouched(self):
        assert normalize_path("django/x.py", repo_root="/repo") == "django/x.py"

    def test_normalize_path_converts_backslashes(self):
        assert normalize_path("django\\x.py") == "django/x.py"

    def test_normalize_path_strips_dot_slash_prefix(self):
        assert normalize_path("./django/x.py") == "django/x.py"

    def test_normalize_path_no_repo_root_returns_as_is(self):
        assert normalize_path("django/x.py") == "django/x.py"


class TestWastedReadRatio:
    def test_wasted_read_ratio_all_relevant_is_zero(self):
        ratio = wasted_read_ratio(["a.py", "b.py"], relevant_files=["a.py", "b.py"])
        assert ratio == 0.0

    def test_wasted_read_ratio_all_wasted_is_one(self):
        ratio = wasted_read_ratio(["c.py"], relevant_files=["a.py"])
        assert ratio == 1.0

    def test_wasted_read_ratio_partial(self):
        ratio = wasted_read_ratio(["a.py", "c.py"], relevant_files=["a.py"])
        assert ratio == 0.5

    def test_wasted_read_ratio_empty_files_returns_none(self):
        assert wasted_read_ratio([], relevant_files=["a.py"]) is None

    def test_wasted_read_ratio_acceptable_extra_not_wasted(self):
        ratio = wasted_read_ratio(
            ["a.py", "extra.py"], relevant_files=["a.py"], acceptable_extra_files=["extra.py"]
        )
        assert ratio == 0.0

    def test_wasted_read_ratio_normalizes_absolute_vs_relative(self):
        ratio = wasted_read_ratio(
            ["/repo/django/a.py"], relevant_files=["django/a.py"], repo_root="/repo"
        )
        assert ratio == 0.0


class TestFactScore:
    def test_fact_score_all_matched(self):
        facts = [{"any_of": ["foo"]}, {"any_of": ["bar", "baz"]}]
        assert fact_score(facts, "the answer mentions foo and baz") == 1.0

    def test_fact_score_none_matched(self):
        facts = [{"any_of": ["foo"]}]
        assert fact_score(facts, "totally unrelated text") == 0.0

    def test_fact_score_partial(self):
        facts = [{"any_of": ["foo"]}, {"any_of": ["nope"]}]
        assert fact_score(facts, "contains foo only") == 0.5

    def test_fact_score_empty_facts_returns_one(self):
        assert fact_score([], "anything") == 1.0

    def test_fact_score_case_insensitive(self):
        facts = [{"any_of": ["WeakMethod"]}]
        assert fact_score(facts, "uses weakmethod internally") == 1.0


class TestFileRecall:
    def test_file_recall_all_mentioned(self):
        assert file_recall("see django/x.py for details", ["django/x.py"]) == 1.0

    def test_file_recall_none_mentioned(self):
        assert file_recall("no files here", ["django/x.py"]) == 0.0

    def test_file_recall_empty_targets_returns_one(self):
        assert file_recall("anything", []) == 1.0

    def test_file_recall_partial(self):
        recall = file_recall("only django/a.py is mentioned", ["django/a.py", "django/b.py"])
        assert recall == 0.5


class TestMechanicalScore:
    def test_mechanical_score_passes_above_threshold(self):
        gt = {
            "must_mention_files": ["django/dispatch/dispatcher.py"],
            "facts": [{"any_of": ["_live_receivers"]}],
        }
        answer = "The file is django/dispatch/dispatcher.py which uses _live_receivers."
        result = mechanical_score(answer, gt)
        assert result["mechanical_correct"] is True
        assert result["file_recall"] == 1.0
        assert result["fact_score"] == 1.0

    def test_mechanical_score_fails_below_threshold(self):
        gt = {"must_mention_files": ["a.py"], "facts": [{"any_of": ["xyz"]}]}
        result = mechanical_score("totally unrelated answer", gt)
        assert result["mechanical_correct"] is False
        assert result["combined"] == 0.0

    def test_mechanical_score_falls_back_to_relevant_files_when_must_mention_empty(self):
        gt = {"must_mention_files": [], "relevant_files": ["a.py"], "facts": []}
        result = mechanical_score("references a.py here", gt)
        assert result["file_recall"] == 1.0

    def test_mechanical_score_no_files_or_facts_specified(self):
        gt = {"must_mention_files": [], "relevant_files": [], "facts": []}
        result = mechanical_score("anything at all", gt)
        assert result["combined"] == 1.0
        assert result["mechanical_correct"] is True


class TestScoreRun:
    def test_score_run_combines_waste_and_correctness(self):
        gt = {
            "relevant_files": ["a.py"],
            "acceptable_extra_files": [],
            "must_mention_files": ["a.py"],
            "facts": [{"any_of": ["works"]}],
        }
        result = score_run(["a.py", "b.py"], "a.py works fine", gt)
        assert result["wasted_read_ratio"] == 0.5
        assert result["mechanical_correct"] is True


class TestJudgePrompt:
    def test_judge_prompt_includes_question_and_answer(self):
        gt = {"relevant_files": ["a.py"], "facts": [{"any_of": ["foo"]}]}
        prompt = judge_prompt("Where is X?", gt, "It's in a.py")
        assert "Where is X?" in prompt
        assert "It's in a.py" in prompt
        assert "a.py" in prompt

    def test_parse_judge_output_clamps_score(self):
        assert parse_judge_output({"verdict": "good", "score": 1.5})["score"] == 1.0
        assert parse_judge_output({"verdict": "bad", "score": -0.5})["score"] == 0.0

    def test_parse_judge_output_defaults_missing_score(self):
        result = parse_judge_output({"verdict": "unclear"})
        assert result["score"] == 0.0

    def test_parse_judge_output_handles_non_numeric_score(self):
        result = parse_judge_output({"verdict": "x", "score": "not-a-number"})
        assert result["score"] == 0.0
