"""Tests for benchmarks.report: aggregation math and markdown/CSV output."""

import csv
import json

import pytest

from benchmarks.report import (
    aggregate_by_category,
    aggregate_by_condition,
    aggregate_condition,
    group_by,
    iqr,
    load_records,
    median,
    render_markdown,
    write_csv,
)


def _record(**overrides):
    base = {
        "task_id": "t1",
        "category": "conceptual",
        "condition": "baseline",
        "rep": 0,
        "repo": "django",
        "repo_sha": "abc123",
        "model": "claude-sonnet-5",
        "mechanical_correct": True,
        "wasted_read_ratio": 0.5,
        "total_tokens": 1000,
        "fresh_tokens": 900,
        "total_cost_usd": 0.05,
        "num_turns": 3,
        "wall_seconds": 10.0,
        "is_error": False,
        "result_subtype": "success",
    }
    base.update(overrides)
    return base


class TestMedianIqr:
    def test_median_basic(self):
        assert median([1, 2, 3]) == 2

    def test_median_ignores_none(self):
        assert median([1, None, 3]) == 2

    def test_median_empty_returns_none(self):
        assert median([]) is None

    def test_median_all_none_returns_none(self):
        assert median([None, None]) is None

    def test_iqr_needs_at_least_two_values(self):
        assert iqr([1]) is None

    def test_iqr_basic(self):
        result = iqr([1, 2, 3, 4, 5, 6, 7, 8])
        assert result is not None
        assert result > 0


class TestAggregateCondition:
    def test_aggregate_condition_correct_fraction(self):
        records = [_record(mechanical_correct=True), _record(mechanical_correct=False)]
        agg = aggregate_condition(records)
        assert agg["n_reps"] == 2
        assert agg["tasks_correct_frac"] == 0.5

    def test_aggregate_condition_medians(self):
        records = [_record(wasted_read_ratio=0.2), _record(wasted_read_ratio=0.4)]
        agg = aggregate_condition(records)
        assert agg["wasted_read_ratio"] == pytest.approx(0.3)


class TestAggregateByCondition:
    def test_aggregate_by_condition_groups_correctly(self):
        records = [
            _record(condition="baseline", task_id="t1", mechanical_correct=True),
            _record(condition="baseline", task_id="t2", mechanical_correct=False),
            _record(condition="nexus", task_id="t1", mechanical_correct=True),
            _record(condition="nexus", task_id="t2", mechanical_correct=True),
        ]
        agg = aggregate_by_condition(records)
        assert set(agg.keys()) == {"baseline", "nexus"}
        assert agg["baseline"]["n_tasks"] == 2
        assert agg["nexus"]["tasks_correct"] == 2


class TestAggregateByCategory:
    def test_aggregate_by_category_groups_by_category_and_condition(self):
        records = [
            _record(category="conceptual", condition="baseline"),
            _record(category="impact", condition="nexus"),
        ]
        agg = aggregate_by_category(records)
        assert ("conceptual", "baseline") in agg
        assert ("impact", "nexus") in agg


class TestGroupBy:
    def test_group_by_single_key_uses_scalar_key(self):
        records = [_record(condition="baseline"), _record(condition="nexus")]
        groups = group_by(records, "condition")
        assert set(groups.keys()) == {"baseline", "nexus"}
        assert len(groups["baseline"]) == 1

    def test_group_by_multi_key_uses_tuple_key(self):
        records = [
            _record(category="impact", condition="nexus"),
            _record(category="impact", condition="nexus"),
            _record(category="impact", condition="baseline"),
        ]
        groups = group_by(records, "category", "condition")
        assert ("impact", "nexus") in groups
        assert len(groups[("impact", "nexus")]) == 2
        assert len(groups[("impact", "baseline")]) == 1

    def test_group_by_empty_records(self):
        assert group_by([], "condition") == {}


class TestRenderMarkdown:
    def test_render_markdown_empty_records(self):
        md = render_markdown([])
        assert "No records found" in md

    def test_render_markdown_rep_count_uses_max_not_first_record(self):
        # Regression: rep count must reflect the true number of reps, not
        # whichever record is first in the list (order-independent).
        records = [
            _record(rep=2, condition="baseline"),
            _record(rep=0, condition="baseline"),
            _record(rep=1, condition="baseline"),
        ]
        md = render_markdown(records)
        assert "N=3 reps" in md

    def test_render_markdown_includes_conditions_table(self):
        records = [_record(condition="baseline"), _record(condition="nexus")]
        md = render_markdown(records)
        assert "baseline" in md
        assert "nexus" in md
        assert "Wasted-read ratio" in md

    def test_render_markdown_includes_category_breakdown(self):
        records = [_record(category="impact", condition="nexus")]
        md = render_markdown(records)
        assert "By category" in md
        assert "impact" in md


class TestWriteCsvAndLoadRecords:
    def test_write_csv_roundtrip(self, tmp_path):
        records = [_record(task_id="t1"), _record(task_id="t2")]
        out = tmp_path / "report.csv"
        write_csv(records, out)
        with open(out) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]["task_id"] == "t1"

    def test_write_csv_empty_records_noop(self, tmp_path):
        out = tmp_path / "report.csv"
        write_csv([], out)
        assert not out.exists()

    def test_load_records_reads_jsonl_glob(self, tmp_path):
        path = tmp_path / "runs-1.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_record(task_id="a")) + "\n")
            f.write(json.dumps(_record(task_id="b")) + "\n")
        records = load_records([str(tmp_path / "runs-*.jsonl")])
        assert len(records) == 2
        assert {r["task_id"] for r in records} == {"a", "b"}

    def test_load_records_skips_blank_lines(self, tmp_path):
        path = tmp_path / "runs-2.jsonl"
        path.write_text(json.dumps(_record()) + "\n\n")
        records = load_records([str(path)])
        assert len(records) == 1
