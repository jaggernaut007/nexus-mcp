"""Tests for benchmarks.validate_tasks: ground-truth checks against a checkout."""

from benchmarks import validate_tasks as vt


def _task(**truth):
    return {"id": "t1", "ground_truth": truth}


def test_check_task_valid_task_has_no_problems(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    return 1\n")
    task = _task(relevant_files=["a.py"], facts=[{"any_of": ["run"]}], must_mention_files=["a.py"])
    assert vt.check_task(task, tmp_path) == []


def test_check_task_reports_a_missing_file(tmp_path):
    problems = vt.check_task(_task(relevant_files=["gone.py"]), tmp_path)
    assert problems == ["t1: missing file gone.py"]


def test_check_task_reports_a_fact_that_is_not_in_the_files(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    pass\n")
    task = _task(relevant_files=["a.py"], facts=[{"any_of": ["walk", "jump"]}])
    problems = vt.check_task(task, tmp_path)
    assert len(problems) == 1 and "walk" in problems[0]


def test_check_task_one_matching_option_is_enough(tmp_path):
    (tmp_path / "a.py").write_text("def run():\n    pass\n")
    task = _task(relevant_files=["a.py"], facts=[{"any_of": ["walk", "run"]}])
    assert vt.check_task(task, tmp_path) == []


def test_check_task_number_must_fit_the_file_length(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\ny = 2\n")
    assert vt.check_task(_task(relevant_files=["a.py"], facts=[{"any_of": ["2"]}]), tmp_path) == []
    problems = vt.check_task(_task(relevant_files=["a.py"], facts=[{"any_of": ["500"]}]), tmp_path)
    assert len(problems) == 1


def test_check_task_a_file_name_counts_as_a_fact(tmp_path):
    (tmp_path / "compiler.py").write_text("x = 1\n")
    task = _task(relevant_files=["compiler.py"], facts=[{"any_of": ["compiler.py"]}])
    assert vt.check_task(task, tmp_path) == []


def test_validate_shipped_suites_have_six_tasks_and_matching_pins():
    for name, problems in vt.validate().items():
        bad = [p for p in problems if "expected 6" in p or "differs" in p or "unknown task" in p]
        assert bad == [], name
