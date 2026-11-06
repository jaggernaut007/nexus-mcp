"""scripts/publish_results.py: one data file drives every published benchmark number."""

import csv
import html
import importlib.util
import io
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "publish_results", ROOT / "scripts" / "publish_results.py"
)
publish = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(publish)
DATA = json.loads((ROOT / "benchmarks" / "published" / "results.json").read_text())


def test_publish_results_check_passes_on_the_committed_files():
    assert publish.main(["--check"]) == 0


def test_pct_change_uses_a_true_minus_sign():
    assert publish.pct_change(0.446, 0.571) == "−22%"
    assert publish.pct_change(1.1, 1.0) == "+10%"


def test_readme_block_lists_every_run_with_its_change():
    block = publish.readme_block(DATA)
    for run in DATA["runs"]:
        assert run["project"] in block
    assert "-22%" in block and "benchmarks.json" in block


def test_site_block_totals_the_correct_answers():
    block = publish.site_block(DATA)
    nexus = sum(r["nexus"]["correct"] for r in DATA["runs"])
    assert f"{nexus} · " in block


def test_csv_has_two_rows_per_run():
    lines = publish.csv_text(DATA).strip().splitlines()
    assert len(lines) == 1 + 2 * len(DATA["runs"])


def test_page_html_has_dataset_json_ld_and_a_title():
    page = publish.page_html(DATA)
    assert '"@type": "Dataset"' in page and "<title>Nexus-MCP Benchmarks</title>" in page


def test_replace_block_fails_without_markers(tmp_path):
    with pytest.raises(SystemExit):
        publish.replace_block("no markers", "x", tmp_path / "f")


def test_check_fails_when_a_generated_number_is_edited(tmp_path, monkeypatch):
    readme = tmp_path / "README.md"
    original = (ROOT / "README.md").read_text()
    readme.write_text(original.replace("$0.446", "$0.111", 1))
    real_outputs = publish.outputs

    def outputs(data):
        files = real_outputs(data)
        return {readme: files[ROOT / "README.md"]}

    monkeypatch.setattr(publish, "outputs", outputs)
    assert publish.main(["--check"]) == 1


def test_comparison_md_has_one_row_per_condition():
    comp = DATA["comparisons"][0]
    lines = publish.comparison_md(comp)
    rows = [line for line in lines if line.startswith("| ") and "Condition" not in line]
    assert len(rows) == len(comp["conditions"])
    assert "| 164,192 |" in "\n".join(lines)


def test_comparison_html_has_every_task_and_escapes_text():
    comp = dict(DATA["comparisons"][0], title="A <b> test")
    page = publish.comparison_html(comp)
    for task in comp["per_task"]:
        assert task["task"] in page
    assert "A &lt;b&gt; test" in page and "<b>" not in page


def test_comparison_html_lists_every_fairness_rule():
    comp = DATA["comparisons"][0]
    page = publish.comparison_html(comp)
    assert "How the comparison was kept fair" in page
    for rule in comp["fairness"]:
        assert html.escape(rule) in page


def test_page_html_and_llms_block_without_comparisons():
    data = {k: v for k, v in DATA.items() if k != "comparisons"}
    assert "Head to head" not in publish.page_html(data, community=[])
    assert "GitNexus" not in publish.llms_block(data)


# Per-language results (schema v2). The fixture holds sample numbers, not measured ones.
LANGS = json.loads((ROOT / "tests" / "fixtures" / "publish" / "languages.json").read_text())[
    "languages"
]
BEFORE_ONLY, BEFORE_AFTER = LANGS


def test_languages_md_without_languages_is_empty():
    assert publish.languages_md([]) == []
    assert "Results per language" not in publish.readme_block(DATA)


def test_languages_md_before_only_shows_one_value_per_cell():
    lines = publish.languages_md([BEFORE_ONLY])
    row = next(line for line in lines if line.startswith("| C# |"))
    assert "| 2.2.2 |" in row and "| 72% |" in row and "| 2,350 |" in row
    assert "| 11 of 12 |" in row and "→" not in row
    assert "[JamesNK/Newtonsoft.Json](https://github.com/JamesNK/Newtonsoft.Json)" in row


def test_languages_md_before_and_after_side_by_side():
    lines = publish.languages_md([BEFORE_AFTER])
    row = next(line for line in lines if line.startswith("| C++ |"))
    assert "| 2.2.2 → 2.3.0 |" in row
    assert "| 48% → 66% |" in row and "| 6,100 → 4,200 |" in row
    assert "| 0.61 → 1.05 |" in row
    # An unchanged value shows once; a value missing before shows a dash.
    assert "| 30.5 |" in row and "| – → 10 of 12 |" in row
    # The Grep and Read baseline shows the latest run only, with no arrow.
    assert "| 35% |" in row and "| 260,000 |" in row


def test_languages_md_has_one_cell_per_header():
    lines = publish.languages_md(LANGS)
    widths = {line.count("|") for line in lines if line.startswith("|")}
    assert len(widths) == 1


def test_language_cell_missing_after_value():
    getter = publish._num(".0%", "quick_tier", "nexus", "hit_at_1")
    entry = dict(BEFORE_ONLY, after={"nexus_version": "2.3.0"})
    assert publish.language_cell(entry, getter, True) == "72% → –"
    assert publish.language_cell(entry, getter, True, arrow=" to ", missing="n/a") == "72% to n/a"


def test_languages_html_without_languages_is_empty():
    assert publish.languages_html([]) == ""
    assert "Results per language" not in publish.page_html(DATA, community=[])


def test_languages_html_scrolls_in_its_own_box_and_escapes_text():
    section = publish.languages_html(LANGS)
    assert '<div class="scroll"><table class="lang">' in section
    assert "&lt;sample&gt; fmtlib/fmt" in section and "<sample>" not in section
    assert "<td>48% → 66%</td>" in section and "<td>72%</td>" in section
    assert "<code>0123456</code>" in section


def test_page_html_has_the_language_section_and_phone_css():
    page = publish.page_html(dict(DATA, schema_version=2, languages=LANGS), community=[])
    assert "<h2>Results per language</h2>" in page
    assert "overflow-x:auto" in page and "position:sticky" in page


def test_site_block_and_llms_block_list_each_language():
    data = dict(DATA, languages=LANGS)
    site = publish.site_block(data)
    assert "C#, JamesNK/<wbr>Newtonsoft.Json: right file first with Nexus-MCP" in site
    assert "Nexus-MCP 2.2.2 → 2.3.0" in site and '<span class="pill">48% → 66%</span>' in site
    llms = publish.llms_block(data)
    assert "- Language C#, JamesNK/Newtonsoft.Json (250 files, Nexus-MCP 2.2.2)" in llms
    assert "48% to 66% (Nexus-MCP)" in llms and "n/a to 10 of 12" in llms
    assert "→" not in llms.split("Language C++")[1]


def test_readme_block_with_languages():
    block = publish.readme_block(dict(DATA, languages=LANGS))
    assert "Results per language:" in block and "| C++ |" in block


def test_csv_without_languages_keeps_two_rows_per_run_and_one_width():
    rows = list(csv.reader(io.StringIO(publish.csv_text(DATA))))
    assert rows[0] == publish.CSV_HEAD
    assert {len(r) for r in rows} == {len(publish.CSV_HEAD)}


def test_csv_language_rows_before_only_and_before_after():
    rows = list(csv.DictReader(io.StringIO(publish.csv_text(dict(DATA, languages=LANGS)))))
    lang = [r for r in rows if r["language"]]
    cs = [r for r in lang if r["language"] == "C#"]
    assert {r["phase"] for r in cs} == {"before"}
    assert [r["condition"] for r in cs] == [
        "eval_grep_and_read",
        "eval_nexus_mcp",
        "nexus_mcp_index",
        "grep_and_read",
        "nexus_mcp",
    ]
    cpp = [r for r in lang if r["language"] == "C++"]
    # No live run before: three rows before (two eval, one index), five after.
    assert [r["phase"] for r in cpp].count("before") == 3
    assert [r["phase"] for r in cpp].count("after") == 5
    index_after = next(
        r for r in cpp if r["phase"] == "after" and r["condition"] == "nexus_mcp_index"
    )
    assert index_after["calls_per_fn"] == "1.05" and index_after["nexus_version"] == "2.3.0"
    assert index_after["commit"].startswith("fedcba98")


def test_main_rejects_an_unknown_schema_version(tmp_path, monkeypatch):
    bad = tmp_path / "results.json"
    bad.write_text(json.dumps(dict(DATA, schema_version=99)))
    monkeypatch.setattr(publish, "DATA", bad)
    with pytest.raises(SystemExit):
        publish.main(["--check"])


def test_main_accepts_schema_version_1(tmp_path, monkeypatch):
    v1 = tmp_path / "results.json"
    v1.write_text(json.dumps(dict(DATA, schema_version=1)))
    monkeypatch.setattr(publish, "DATA", v1)
    monkeypatch.setattr(publish, "outputs", lambda data: {})
    assert publish.main(["--check"]) == 0


def test_language_setup_with_and_without_fields():
    assert publish.language_setup(BEFORE_ONLY) == "sonnet, medium effort, bare"
    assert publish.language_setup({"model": "opus"}) == "opus"
    assert publish.language_setup({}) == "–"
