"""Write every published benchmark number from one file: benchmarks/published/results.json.

Run: python scripts/publish_results.py          (rewrite the generated blocks and files)
     python scripts/publish_results.py --check  (exit 1 when a generated block is out of date)

Generated blocks sit between `<!-- results:start -->` and `<!-- results:end -->` in README.md,
site/index.html and llms.txt. Do not edit them by hand. The script also writes
site/benchmarks.json, site/benchmarks.csv and site/benchmarks.html.
"""

import argparse
import csv
import html
import io
import json
import re
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "benchmarks" / "published" / "results.json"
COMMUNITY = ROOT / "site" / "data" / "community-results.json"
START, END = "<!-- results:start -->", "<!-- results:end -->"
# Version 2 adds the optional "languages" list (one entry per language, with a "before" run
# and an optional "after" run). Version 1 files have no "languages" key and still work.
SCHEMA_VERSIONS = (1, 2)
SITE_URL = "https://jaggernaut007.github.io/nexus-mcp"
REPO_URL = "https://github.com/jaggernaut007/Nexus-MCP"


def pct_change(new: float, old: float) -> str:
    change = (new - old) / old * 100 if old else 0.0
    sign = "−" if change < 0 else "+"
    return f"{sign}{abs(change):.0f}%"


def ascii_pct(new: float, old: float) -> str:
    return pct_change(new, old).replace("−", "-")


def total_correct(data: Dict, side: str) -> str:
    runs = data["runs"]
    return f"{sum(r[side]['correct'] for r in runs)} of {sum(r[side]['of'] for r in runs)}"


def comparison_md(comp: Dict) -> List[str]:
    """A head-to-head comparison of tools as Markdown lines: one row per condition."""
    name = f"[{comp['project']}]({comp['url']})" if comp.get("url") else comp["project"]
    lines = [
        f"{comp['title']} on {name} ({comp['language']}, {comp['files']:,} files): "
        f"{comp['tasks']} tasks, {comp['runs_per_task']} runs each, {comp['date']}.",
        "",
        "| Condition | Version | Tokens (median) | Cost per task (median) | Correct |",
        "|---|---|---:|---:|---:|",
    ]
    for c in comp["conditions"]:
        lines.append(
            f"| {c['label']} | {c['version']} | {c['median_tokens']:,} | "
            f"${c['median_cost_usd']:.3f} | {c['correct']} of {c['of']} |"
        )
    lines += [
        "",
        " ".join(comp["notes"][1:3]) + " Each condition had the same agent, model, prompt and "
        "built-in tools, and each tool had its MCP server and its always-loaded instructions "
        f"([method]({REPO_URL}/blob/main/benchmarks/README.md#methodology)).",
    ]
    return lines


def _get(phase: Optional[Dict], *keys: str):
    """The value at keys in a nested dict, or None when a key is missing."""
    cur = phase
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _num(fmt: str, *keys: str) -> Callable[[Dict], Optional[str]]:
    """A getter that formats the number at keys, or gives None when it is missing."""

    def get(phase: Dict) -> Optional[str]:
        value = _get(phase, *keys)
        return None if value is None else format(value, fmt)

    return get


def _correct(side: str) -> Callable[[Dict], Optional[str]]:
    def get(phase: Dict) -> Optional[str]:
        live = _get(phase, "live", side)
        if not isinstance(live, dict) or live.get("correct") is None:
            return None
        return f"{live['correct']} of {live['of']}"

    return get


def _text(*keys: str) -> Callable[[Dict], Optional[str]]:
    def get(phase: Dict) -> Optional[str]:
        value = _get(phase, *keys)
        return None if value is None else str(value)

    return get


# Columns of the per-language table: (header, getter, compare). A column with compare=True
# shows "before → after" when the entry has an "after" run. A column with compare=False is the
# Grep and Read baseline: it does not change with the Nexus-MCP version, so it shows the
# latest run only.
LANGUAGE_COLUMNS = [
    ("Nexus-MCP version", _text("nexus_version"), True),
    ("Index time (s)", _num(",.1f", "index_seconds"), True),
    ("Right file first: Grep and Read", _num(".0%", "quick_tier", "grep", "hit_at_1"), False),
    ("Right file first: Nexus-MCP", _num(".0%", "quick_tier", "nexus", "hit_at_1"), True),
    (
        "Tokens to the right file: Grep and Read",
        _num(",.0f", "quick_tier", "grep", "median_tokens"),
        False,
    ),
    (
        "Tokens to the right file: Nexus-MCP",
        _num(",.0f", "quick_tier", "nexus", "median_tokens"),
        True,
    ),
    ("Search hit@1", _num(".2f", "retrieval_hit_at_1"), True),
    ("Calls per function", _num(".2f", "graph", "calls_per_fn"), True),
    ("Live tokens: Grep and Read", _num(",.0f", "live", "baseline", "median_tokens"), False),
    ("Live tokens: Nexus-MCP", _num(",.0f", "live", "nexus", "median_tokens"), True),
    ("Correct: Grep and Read", _correct("baseline"), False),
    ("Correct: Nexus-MCP", _correct("nexus"), True),
]
LANGUAGE_NOTE = (
    "One row for each language, on one pinned public project. A Nexus-MCP cell with an arrow "
    "shows the value before and after a fix: before → after. A Grep and Read cell shows the "
    "latest run. Right file first and tokens come from `nexus-mcp-ci eval` (no LLM); search "
    "hit@1 from the retrieval suite; live tokens and correct answers from the live benchmark."
)


def language_cell(
    entry: Dict,
    getter: Callable[[Dict], Optional[str]],
    compare: bool,
    arrow: str = " → ",
    missing: str = "–",
) -> str:
    """One cell: the "before" value, or "before → after" when both runs exist and differ."""
    before, after = entry.get("before") or {}, entry.get("after")
    if not compare:
        return getter(after or before) or missing
    first = getter(before) or missing
    if not after:
        return first
    second = getter(after) or missing
    return first if second == first else f"{first}{arrow}{second}"


def language_setup(entry: Dict) -> str:
    """The agent setup of the live runs, such as "sonnet, medium effort, bare"."""
    effort = f"{entry['effort']} effort" if entry.get("effort") else None
    parts = [p for p in (entry.get("model"), effort, entry.get("isolation")) if p]
    return ", ".join(str(p) for p in parts) or "–"


def languages_md(langs: List[Dict]) -> List[str]:
    """The per-language results as Markdown lines. No lines when there are no languages."""
    if not langs:
        return []
    heads = ["Language", "Project", *(h for h, _, _ in LANGUAGE_COLUMNS), "Agent"]
    lines = [
        "Results per language:",
        "",
        "| " + " | ".join(heads) + " |",
        "|---|---|" + "---:|" * len(LANGUAGE_COLUMNS) + "---|",
    ]
    for e in langs:
        name = f"[{e['project']}]({e['url']})" if e.get("url") else e["project"]
        cells = [language_cell(e, g, c) for _, g, c in LANGUAGE_COLUMNS]
        lines.append(
            f"| {e['language']} | {name} ({e['files']:,} files) | "
            + " | ".join(cells)
            + f" | {language_setup(e)} |"
        )
    return [*lines, "", LANGUAGE_NOTE]


def readme_block(data: Dict) -> str:
    m = data["method"]
    lines = [
        "| Project | Cost: Grep and Read → Nexus-MCP | Turns | Correct answers |",
        "|---|---:|---:|---:|",
    ]
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        name = f"[{r['project']}]({r['url']})" if r.get("url") else r["project"]
        lines.append(
            f"| {name} ({r['files']:,} files) | ${b['cost_usd']:.3f} → ${n['cost_usd']:.3f} "
            f"(**{ascii_pct(n['cost_usd'], b['cost_usd'])}**) | {b['turns']} → {n['turns']} | "
            f"{b['correct']} → {n['correct']} of {n['of']} |"
        )
    ret = data["retrieval"]
    suites = ", ".join(f"`{k}` {v:.2f}" for k, v in ret["suites"].items())
    lines += [
        "",
        f"Live runs: {m['agent']}, {m['questions_per_project']} questions per project, "
        f"{m['runs_per_question']} runs each, version {data['version']} ({data['updated']}). "
        f"{m['noise']} Search {ret['metric']} on the four retrieval suites: {suites}.",
        "",
        f"`nexus-mcp-ci eval` (the free offline check, no LLM) on {len(data['quick_tier'])} projects:",
        "",
        "| Project | Verdict | Median tokens to the right file | Right file first |",
        "|---|---|---:|---:|",
    ]
    for q in data["quick_tier"]:
        name = f"[{q['project']}]({q['url']})" if q.get("url") else q["project"]
        lines.append(
            f"| {name} ({q['files']:,} files) | {q['verdict']} | "
            f"{q['grep']['median_tokens']:,} → **{q['nexus']['median_tokens']:,}** | "
            f"{q['grep']['hit_at_1']:.0%} → {q['nexus']['hit_at_1']:.0%} |"
        )
    if data.get("languages"):
        lines += ["", *languages_md(data["languages"])]
    for c in data.get("comparisons", []):
        lines += ["", *comparison_md(c)]
    lines += [
        "",
        f"All numbers: [benchmarks page]({SITE_URL}/benchmarks.html) · "
        f"[JSON]({SITE_URL}/benchmarks.json) · [CSV]({SITE_URL}/benchmarks.csv).",
    ]
    return "\n".join(lines)


def site_block(data: Dict) -> str:
    rows: List[str] = []
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        label = f"{html.escape(r['project'])}, {r['files']:,} files"
        change = pct_change(n["cost_usd"], b["cost_usd"])
        muted = "" if (n["cost_usd"] - b["cost_usd"]) / b["cost_usd"] <= -0.10 else " muted"
        rows.append(
            f'<tr><td>{label}: turns</td><td><span class="pill">'
            f"{b['turns']} → {n['turns']}</span></td></tr>"
        )
        rows.append(
            f'<tr><td>{html.escape(r["project"])}: cost</td><td><span class="pill{muted}">'
            f"{change}</span></td></tr>"
        )
    rows.append(
        '<tr><td>Correct answers: Nexus, then Grep and Read</td><td><span class="pill muted">'
        f"{total_correct(data, 'nexus').split(' of ')[0]} · {total_correct(data, 'baseline')}"
        "</span></td></tr>"
    )
    for q in data["quick_tier"]:
        g, n = q["grep"]["median_tokens"], q["nexus"]["median_tokens"]
        rows.append(
            f"<tr><td>{html.escape(q['project'])}, {q['files']:,} files: tokens to the right "
            f'file (<code>eval</code>, no LLM)</td><td><span class="pill">{g:,} → {n:,}'
            "</span></td></tr>"
        )
    for e in data.get("languages", []):
        # A break point after each "/" lets a long owner/repo name wrap at phone width.
        project = html.escape(e["project"]).replace("/", "/<wbr>")
        label = f"{html.escape(e['language'])}, {project}: right file first"
        version = language_cell(e, _text("nexus_version"), True)
        label += f", Nexus-MCP {html.escape(version)}" if e.get("after") else " with Nexus-MCP"
        hit = language_cell(e, _num(".0%", "quick_tier", "nexus", "hit_at_1"), True)
        rows.append(
            f'<tr><td>{label} (<code>eval</code>, no LLM)</td><td><span class="pill">'
            f"{html.escape(hit)}</span></td></tr>"
        )
    return "\n".join("            " + row for row in rows)


def llms_block(data: Dict) -> str:
    lines = [
        "## Benchmarks",
        "",
        f"Source of truth: {SITE_URL}/benchmarks.json (updated {data['updated']}, "
        f"version {data['version']}). Method: {data['method']['agent']}, "
        f"{data['method']['runs_per_question']} runs per question.",
        "",
    ]
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        lines.append(
            f"- {r['project']} ({r['files']} files): cost ${b['cost_usd']:.3f} with Grep and "
            f"Read, ${n['cost_usd']:.3f} with Nexus-MCP ({ascii_pct(n['cost_usd'], b['cost_usd'])}); "
            f"turns {b['turns']} to {n['turns']}; correct {b['correct']} and {n['correct']} "
            f"of {n['of']}."
        )
    for q in data["quick_tier"]:
        lines.append(
            f"- Quick tier (`nexus-mcp-ci eval`, no LLM), {q['project']} ({q['files']} files): "
            f"{q['verdict']}; median tokens to the right file {q['grep']['median_tokens']} "
            f"(Grep and Read) and {q['nexus']['median_tokens']} (Nexus-MCP); right file first "
            f"{q['grep']['hit_at_1']:.0%} and {q['nexus']['hit_at_1']:.0%}."
        )
    for comp in data.get("comparisons", []):
        conds = "; ".join(
            f"{c['label']} {c['version']}: {c['median_tokens']} tokens, "
            f"${c['median_cost_usd']:.3f}, {c['correct']} of {c['of']} correct"
            for c in comp["conditions"]
        )
        lines.append(
            f"- {comp['title']}, {comp['project']} ({comp['language']}, {comp['tasks']} tasks, "
            f"{comp['runs_per_task']} runs each, medians per task): {conds}."
        )
    lines += languages_llms(data.get("languages", []))
    lines.append(f"- {data['method']['noise']}")
    return "\n".join(lines)


def languages_llms(langs: List[Dict]) -> List[str]:
    """One plain-text line for each language, for llms.txt ("to" in place of an arrow)."""
    lines = []
    for e in langs:
        cells = {
            h: language_cell(e, g, c, arrow=" to ", missing="n/a") for h, g, c in LANGUAGE_COLUMNS
        }
        lines.append(
            f"- Language {e['language']}, {e['project']} ({e['files']} files, "
            f"Nexus-MCP {cells['Nexus-MCP version']}): right file first "
            f"{cells['Right file first: Grep and Read']} (Grep and Read) and "
            f"{cells['Right file first: Nexus-MCP']} (Nexus-MCP); median tokens to the right file "
            f"{cells['Tokens to the right file: Grep and Read']} and "
            f"{cells['Tokens to the right file: Nexus-MCP']}; search hit@1 {cells['Search hit@1']}; "
            f"calls per function {cells['Calls per function']}; live median tokens "
            f"{cells['Live tokens: Grep and Read']} and {cells['Live tokens: Nexus-MCP']}; "
            f"correct {cells['Correct: Grep and Read']} and {cells['Correct: Nexus-MCP']}."
        )
    return lines


CSV_HEAD = [
    "project", "files", "commit", "date", "condition", "cost_usd", "turns", "correct", "of",
    "language", "phase", "nexus_version", "median_tokens", "hit_at_1",
    "index_seconds", "fn_nodes", "calls_per_fn",
]  # fmt: skip


def language_csv_rows(langs: List[Dict]) -> List[List]:
    """CSV rows for each language and run ("before", "after"): one row per condition.

    Conditions: eval_grep_and_read and eval_nexus_mcp (quick tier), nexus_mcp_index (index
    time, graph and search hit@1), grep_and_read and nexus_mcp (live). A missing part of a run
    gives no row.
    """
    rows = []
    for e in langs:
        for phase_name in ("before", "after"):
            p = e.get(phase_name)
            if not p:
                continue

            def row(condition: str, **values) -> List:
                cols = {
                    "project": e["project"], "files": e["files"], "commit": e.get("pin"),
                    "date": p.get("date"), "condition": condition, "language": e["language"],
                    "phase": phase_name, "nexus_version": p.get("nexus_version"), **values,
                }  # fmt: skip
                return ["" if cols.get(k) is None else cols[k] for k in CSV_HEAD]

            for side, name in (("grep", "eval_grep_and_read"), ("nexus", "eval_nexus_mcp")):
                q = _get(p, "quick_tier", side)
                if isinstance(q, dict):
                    rows.append(
                        row(name, median_tokens=q.get("median_tokens"), hit_at_1=q.get("hit_at_1"))
                    )
            graph = p.get("graph") or {}
            index = {
                "hit_at_1": p.get("retrieval_hit_at_1"),
                "index_seconds": p.get("index_seconds"),
                "fn_nodes": graph.get("fn_nodes"),
                "calls_per_fn": graph.get("calls_per_fn"),
            }
            if any(v is not None for v in index.values()):
                rows.append(row("nexus_mcp_index", **index))
            for side, name in (("baseline", "grep_and_read"), ("nexus", "nexus_mcp")):
                s = _get(p, "live", side)
                if isinstance(s, dict):
                    rows.append(
                        row(
                            name,
                            cost_usd=s.get("cost_usd"),
                            correct=s.get("correct"),
                            of=s.get("of"),
                            median_tokens=s.get("median_tokens"),
                        )
                    )
    return rows


def csv_text(data: Dict) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(CSV_HEAD)
    blank = [""] * (len(CSV_HEAD) - 9)
    for r in data["runs"]:
        for side, name in (("baseline", "grep_and_read"), ("nexus", "nexus_mcp")):
            s = r[side]
            writer.writerow(
                [
                    r["project"],
                    r["files"],
                    r["commit"],
                    r["date"],
                    name,
                    s["cost_usd"],
                    s["turns"],
                    s["correct"],
                    s["of"],
                    *blank,
                ]
            )
    writer.writerows(language_csv_rows(data.get("languages", [])))
    return out.getvalue()


def load_community() -> List[Dict]:
    """Shared results that a maintainer accepted (see scripts/ingest_community_result.py)."""
    return json.loads(COMMUNITY.read_text()) if COMMUNITY.exists() else []


def community_html(entries: List[Dict]) -> str:
    """The community table. Values were validated on ingest; they are escaped again here."""
    head = (
        "<h2>Community results</h2>\n<p>Runs shared with <code>nexus-mcp-ci eval share</code> "
        "and accepted by a maintainer. Numbers only: no code and no private names.</p>\n"
    )
    if not entries:
        return head + "<p>None yet. Yours can be the first.</p>\n"
    rows = []
    for e in entries:
        quick = e.get("quick")
        if quick:
            g, n = quick["grep"]["median_tokens"], quick["nexus"]["median_tokens"]
            detail = f"{html.escape(quick['verdict'])}; tokens {g:,.0f} → {n:,.0f}"
        else:
            rec = e.get("agent", {}).get("recommendation", {})
            detail = f"best: {html.escape(str(rec.get('spec')))} {html.escape(str(rec.get('condition')))}"
        src = e["source"]
        project = (
            f'<a href="{html.escape(src["url"])}">{html.escape(src["url"].split("github.com/")[1])}</a>'
            if src.get("url")
            else html.escape(src["kind"])
        )
        rows.append(
            f"<tr><td>#{int(e['issue'])}</td><td>{html.escape(e['created'])}</td>"
            f"<td>{html.escape(e['tier'])}</td><td>{project}</td>"
            f"<td>{html.escape(e['repo']['size_band'])}</td>"
            f"<td>{html.escape(', '.join(e['repo']['languages']))}</td><td>{detail}</td>"
            f"<td>{html.escape(e['nexus_mcp_ci'])}</td></tr>"
        )
    return (
        head + '<div class="scroll"><table><thead><tr><th>Issue</th><th>Date</th><th>Tier</th>'
        "<th>Project</th><th>Size</th><th>Languages</th><th>Result</th><th>Version</th>"
        "</tr></thead><tbody>\n" + "\n".join(rows) + "\n</tbody></table></div>\n"
    )


def comparison_html(comp: Dict) -> str:
    """A head-to-head comparison section: totals per condition, then each task."""
    conds = comp["conditions"]
    name = html.escape(comp["project"])
    if comp.get("url"):
        name = f'<a href="{html.escape(comp["url"])}">{name}</a>'
    summary = "".join(
        f"<tr><td>{html.escape(c['label'])}</td><td>{html.escape(c['version'])}</td>"
        f"<td>{c['median_tokens']:,}</td><td>${c['median_cost_usd']:.3f}</td>"
        f"<td>${c['total_cost_usd']:.2f}</td><td>{c['median_turns']:g}</td>"
        f"<td>{c['correct']} of {c['of']}</td></tr>"
        for c in conds
    )
    heads = "".join(f"<th>{html.escape(c['label'])}</th>" for c in conds)
    tasks = "".join(
        f"<tr><td><code>{html.escape(t['task'])}</code></td><td>{html.escape(t['category'])}</td>"
        + "".join(
            f"<td>{t['tokens'][c['key']]:,} · ${t['cost_usd'][c['key']]:.3f}</td>" for c in conds
        )
        + "</tr>"
        for t in comp["per_task"]
    )
    notes = "".join(f"<li>{html.escape(n)}</li>" for n in comp["notes"])
    fairness = "".join(f"<li>{html.escape(n)}</li>" for n in comp.get("fairness", []))
    return f"""<h2>Head to head: {html.escape(comp["title"])}</h2>
<p>{name} ({html.escape(comp["language"])}, {comp["files"]:,} files, commit <code>{html.escape(comp["commit"])}</code>): {comp["tasks"]} tasks, {comp["runs_per_task"]} runs each, {html.escape(comp["agent"])}, {html.escape(comp["date"])}. Each total is {html.escape(comp["aggregate"])}.</p>
<div class="scroll"><table><thead><tr><th>Condition</th><th>Version</th><th>Tokens (median)</th><th>Cost per task (median)</th><th>Cost of all runs</th><th>Turns</th><th>Correct</th></tr></thead><tbody>{summary}</tbody></table></div>
<div class="scroll"><table><thead><tr><th>Task</th><th>Category</th>{heads}</tr></thead><tbody>{tasks}</tbody></table></div>
<ul>{notes}</ul>
<h3>How the comparison was kept fair</h3>
<ul>{fairness}</ul>
<p>The tasks are in <code>{html.escape(comp["task_suite"])}</code>. The method, and how to run the benchmark on any repo, are in <a href="{REPO_URL}/blob/main/benchmarks/README.md">benchmarks/README.md</a>.</p>
"""


def languages_html(langs: List[Dict]) -> str:
    """The per-language section of the benchmarks page. Empty when there are no languages.

    The table scrolls in its own box (class "scroll") and keeps the language column in view,
    so a phone gets no horizontal page scroll.
    """
    if not langs:
        return ""
    heads = "".join(
        f"<th>{html.escape(h)}</th>"
        for h in ("Language", "Project", *(h for h, _, _ in LANGUAGE_COLUMNS), "Agent")
    )
    rows = []
    for e in langs:
        name = html.escape(e["project"])
        if e.get("url"):
            name = f'<a href="{html.escape(e["url"])}">{name}</a>'
        pin = f", <code>{html.escape(str(e['pin'])[:7])}</code>" if e.get("pin") else ""
        cells = "".join(
            f"<td>{html.escape(language_cell(e, g, c))}</td>" for _, g, c in LANGUAGE_COLUMNS
        )
        rows.append(
            f'<tr><th scope="row">{html.escape(e["language"])}</th>'
            f"<td>{name} ({e['files']:,} files{pin})</td>{cells}"
            f"<td>{html.escape(language_setup(e))}</td></tr>"
        )
    note = re.sub(r"`([^`]+)`", r"<code>\1</code>", html.escape(LANGUAGE_NOTE))
    return (
        f"<h2>Results per language</h2>\n<p>{note}</p>\n"
        f'<div class="scroll"><table class="lang"><thead><tr>{heads}</tr></thead><tbody>'
        + "".join(rows)
        + "</tbody></table></div>\n"
    )


def page_html(data: Dict, community: Optional[List[Dict]] = None) -> str:
    community = load_community() if community is None else community
    m = data["method"]
    rows = []
    for r in data["runs"]:
        b, n = r["baseline"], r["nexus"]
        name = html.escape(r["project"])
        if r.get("url"):
            name = f'<a href="{html.escape(r["url"])}">{name}</a>'
        rows.append(
            f"<tr><td>{name}</td><td>{r['files']:,}</td><td>${b['cost_usd']:.3f}</td>"
            f"<td>${n['cost_usd']:.3f}</td><td>{pct_change(n['cost_usd'], b['cost_usd'])}</td>"
            f"<td>{b['turns']} → {n['turns']}</td><td>{b['correct']} / {n['correct']} of "
            f"{n['of']}</td><td><code>{r['commit']}</code>, {r['date']}</td></tr>"
        )
    suites = "".join(
        f"<tr><td><code>{html.escape(k)}</code></td><td>{v:.2f}</td></tr>"
        for k, v in data["retrieval"]["suites"].items()
    )
    quick_rows = []
    for q in data["quick_tier"]:
        name = html.escape(q["project"])
        if q.get("url"):
            name = f'<a href="{html.escape(q["url"])}">{name}</a>'
        quick_rows.append(
            f"<tr><td>{name}</td><td>{q['files']:,}</td><td>{q['questions']}</td>"
            f"<td>{html.escape(q['verdict'])}</td><td>{q['grep']['median_tokens']:,}</td>"
            f"<td>{q['nexus']['median_tokens']:,}</td><td>{q['grep']['hit_at_1']:.0%} → "
            f"{q['nexus']['hit_at_1']:.0%}</td><td>{q['grep']['hit_at_3']:.0%} → "
            f"{q['nexus']['hit_at_3']:.0%}</td><td>{html.escape(q['date'])}</td></tr>"
        )
    quick_table = (
        '<div class="scroll"><table><thead><tr><th>Project</th><th>Files</th><th>Questions</th>'
        "<th>Verdict</th><th>Tokens: Grep and Read</th><th>Tokens: Nexus-MCP</th>"
        "<th>Right file first</th><th>Top 3</th><th>Date</th></tr></thead><tbody>"
        + "".join(quick_rows)
        + "</tbody></table></div>"
    )
    dataset = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": "Nexus-MCP token-efficiency benchmark",
        "description": "Live agent runs of Claude Code with Nexus-MCP against Grep and Read: "
        "cost, turns and correct answers per project.",
        "url": f"{SITE_URL}/benchmarks.html",
        "dateModified": data["updated"],
        "license": f"{REPO_URL}/blob/main/LICENSE",
        "creator": {"@type": "Person", "name": "Shreyas Jagannath"},
        "distribution": [
            {
                "@type": "DataDownload",
                "encodingFormat": "application/json",
                "contentUrl": f"{SITE_URL}/benchmarks.json",
            },
            {
                "@type": "DataDownload",
                "encodingFormat": "text/csv",
                "contentUrl": f"{SITE_URL}/benchmarks.csv",
            },
        ],
    }
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nexus-MCP Benchmarks</title>
<meta name="description" content="Measured cost, turns and accuracy of Claude Code with the Nexus-MCP code-intelligence server against Grep and Read, with method and raw data.">
<link rel="canonical" href="{SITE_URL}/benchmarks.html">
<script type="application/ld+json">{json.dumps(dataset)}</script>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1b18; --muted:#5f5a52; --line:#e3ded4; --accent:#0f6b5c; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ --bg:#14130f; --fg:#ece8df; --muted:#a39d91; --line:#2e2b25; --accent:#5cc7b2; }} }}
:root[data-theme="dark"] {{ --bg:#14130f; --fg:#ece8df; --muted:#a39d91; --line:#2e2b25; --accent:#5cc7b2; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:16px/1.6 system-ui, sans-serif; }}
main {{ max-width:960px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:2rem; margin:0 0 8px; }} h2 {{ margin-top:40px; }}
p, li {{ color:var(--muted); }} a {{ color:var(--accent); }}
.scroll {{ overflow-x:auto; max-width:100%; }}
table {{ border-collapse:collapse; width:100%; font-variant-numeric:tabular-nums; }}
th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); white-space:nowrap; }}
th {{ font-size:.85rem; color:var(--muted); font-weight:600; }}
code {{ font-size:.9em; }}
.lang tr > :first-child {{ position:sticky; left:0; background:var(--bg); color:var(--fg); }}
</style>
</head>
<body>
<main>
<p><a href="./">← Nexus-MCP</a></p>
<h1>Benchmarks</h1>
<p>Nexus-MCP is a local code-intelligence MCP server. These are live runs of {html.escape(m["agent"])} with Nexus-MCP against an agent with only Grep, Glob and Read. Updated {data["updated"]}, version {data["version"]}.</p>
<h2>Live agent runs</h2>
<div class="scroll"><table>
<thead><tr><th>Project</th><th>Files</th><th>Cost: Grep and Read</th><th>Cost: Nexus-MCP</th><th>Change</th><th>Turns</th><th>Correct (Grep / Nexus)</th><th>Commit, date</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table></div>
<p>{m["questions_per_project"]} questions per project, {m["runs_per_question"]} runs each. {html.escape(m["noise"])} The saving is large on code the model does not know and small on code it knows from training.</p>
<h2>Search accuracy (no LLM)</h2>
<div class="scroll"><table><thead><tr><th>Retrieval suite</th><th>{html.escape(data["retrieval"]["metric"])}</th></tr></thead><tbody>{suites}</tbody></table></div>
<h2>Quick tier: <code>nexus-mcp-ci eval</code> (no LLM)</h2>
<p>The free offline check: about 30 to 40 questions made from each project, with the right file from the parser. Tokens are a proxy (characters divided by 4). Chromium <code>base/</code> took 25 minutes to index on a laptop; the Nexus-MCP source takes about 30 seconds (<code>--example</code>).</p>
{quick_table}
{languages_html(data.get("languages", []))}{"".join(comparison_html(c) for c in data.get("comparisons", []))}{community_html(community)}<h2>Run it on your own code</h2>
<p><code>uvx nexus-mcp-ci eval</code> runs the free offline check on the current folder. <code>uvx nexus-mcp-ci eval --agent claude</code> runs the paid A/B with your own Claude Code login, after it shows the cost.</p>
<h2>Data</h2>
<ul>
<li><a href="benchmarks.json">benchmarks.json</a> (source of truth)</li>
<li><a href="benchmarks.csv">benchmarks.csv</a></li>
<li><a href="{html.escape(m["source"])}">Method and run history (docs/EVALS.md)</a></li>
</ul>
</main>
</body>
</html>
"""


def replace_block(text: str, block: str, path: Path) -> str:
    pattern = re.compile(re.escape(START) + r".*?" + re.escape(END), re.S)
    if not pattern.search(text):
        raise SystemExit(f"{path}: markers {START} ... {END} not found")
    # Keep the indent of the start marker, so the end marker lines up with it.
    match = pattern.search(text)
    line_start = text.rfind("\n", 0, match.start()) + 1
    indent = text[line_start : match.start()]
    indent = indent if not indent.strip() else ""
    return pattern.sub(lambda _: f"{START}\n{block}\n{indent}{END}", text, count=1)


def outputs(data: Dict) -> Dict[Path, str]:
    files = {
        ROOT / "site" / "benchmarks.json": json.dumps(data, indent=2) + "\n",
        ROOT / "site" / "benchmarks.csv": csv_text(data),
        ROOT / "site" / "benchmarks.html": page_html(data),
    }
    for rel, block in (
        ("README.md", readme_block(data)),
        ("site/index.html", site_block(data)),
        ("llms.txt", llms_block(data)),
    ):
        path = ROOT / rel
        files[path] = replace_block(path.read_text(), block, path)
    return files


def _show(path: Path) -> str:
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="Fail when output is out of date")
    args = parser.parse_args(argv)
    data = json.loads(DATA.read_text())
    if data.get("schema_version") not in SCHEMA_VERSIONS:
        raise SystemExit(f"{_show(DATA)}: schema_version must be one of {SCHEMA_VERSIONS}")
    stale = []
    for path, text in outputs(data).items():
        current = path.read_text() if path.exists() else None
        if current == text:
            continue
        if args.check:
            stale.append(_show(path))
        else:
            path.write_text(text)
            print(f"wrote {_show(path)}")
    if stale:
        print("Out of date (run python scripts/publish_results.py):", *stale, sep="\n  ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
