"""Tests for benchmarks.report: aggregation math and markdown/CSV output."""

import csv
import json

import pytest

from benchmarks.report import (
    _fmt,
    aggregate_by_category,
    aggregate_by_condition,
    aggregate_condition,
    group_by,
    iqr,
    load_records,
    main,
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

    def test_iqr_needs_at_least_two_values_after_filtering_none(self):
        # Only one non-None value survives filtering — same "too few" case
        # as test_iqr_needs_at_least_two_values, but via the filter path.
        assert iqr([1, None]) is None

    def test_iqr_all_none_returns_none(self):
        assert iqr([None, None]) is None


class TestFmt:
    def test_fmt_none_returns_em_dash(self):
        assert _fmt(None) == "—"

    def test_fmt_float_uses_digits(self):
        assert _fmt(0.333333, digits=2) == "0.33"

    def test_fmt_float_with_suffix(self):
        assert _fmt(12.5, suffix="s") == "12.50s"

    def test_fmt_non_float_passthrough(self):
        assert _fmt(3, suffix="x") == "3x"


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

    def test_aggregate_by_condition_all_none_wasted_ratio_stays_none(self):
        # Every run had zero files touched (wasted_read_ratio=None per-run) —
        # the aggregate must stay None, not silently become 0.0.
        records = [
            _record(condition="baseline", task_id="t1", wasted_read_ratio=None),
            _record(condition="baseline", task_id="t2", wasted_read_ratio=None),
        ]
        agg = aggregate_by_condition(records)
        assert agg["baseline"]["wasted_read_ratio"] is None
        assert agg["baseline"]["wasted_read_ratio_iqr"] is None


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

    def test_render_markdown_all_none_wasted_ratio_shows_em_dash(self):
        records = [_record(condition="baseline", wasted_read_ratio=None)]
        md = render_markdown(records)
        assert "—" in md

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

    def test_load_records_skips_malformed_line_and_keeps_rest(self, tmp_path, capsys):
        path = tmp_path / "runs-3.jsonl"
        with open(path, "w") as f:
            f.write(json.dumps(_record(task_id="good1")) + "\n")
            f.write("{not valid json\n")
            f.write(json.dumps(_record(task_id="good2")) + "\n")

        records = load_records([str(path)])

        assert {r["task_id"] for r in records} == {"good1", "good2"}
        assert "malformed" in capsys.readouterr().err.lower()


class TestMain:
    def test_main_writes_markdown_to_out_path(self, tmp_path):
        jsonl_path = tmp_path / "runs.jsonl"
        jsonl_path.write_text(json.dumps(_record()) + "\n")
        out_path = tmp_path / "report.md"

        rc = main([str(jsonl_path), "--out", str(out_path)])

        assert rc == 0
        assert out_path.exists()
        assert "Token-efficiency benchmark" in out_path.read_text()

    def test_main_writes_csv_when_requested(self, tmp_path):
        jsonl_path = tmp_path / "runs.jsonl"
        jsonl_path.write_text(json.dumps(_record()) + "\n")
        csv_path = tmp_path / "report.csv"

        main([str(jsonl_path), "--csv", str(csv_path)])

        assert csv_path.exists()
        with open(csv_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1

    def test_main_prints_to_stdout_without_out(self, tmp_path, capsys):
        jsonl_path = tmp_path / "runs.jsonl"
        jsonl_path.write_text(json.dumps(_record()) + "\n")

        main([str(jsonl_path)])

        assert "Token-efficiency benchmark" in capsys.readouterr().out

    def test_main_no_matching_files_reports_no_records(self, tmp_path, capsys):
        rc = main([str(tmp_path / "nonexistent-*.jsonl")])
        assert rc == 0
        assert "No records found" in capsys.readouterr().out
