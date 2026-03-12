#!/usr/bin/env python3
"""
Nexus-MCP Self-Test Demo
=========================
Exercises all 13 MCP tools end-to-end by calling the underlying functions
directly (bypassing the MCP protocol transport layer).

Usage:
    python self_test/demo_mcp.py [/path/to/project]

If no path is given, a small sample project is created in a temp directory.
"""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Rich console setup (graceful degradation if rich not installed)
# ---------------------------------------------------------------------------
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

    class _FallbackConsole:
        """Minimal stand-in when rich is not installed."""

        @staticmethod
        def print(*args, **kwargs):
            kwargs.pop("style", None)
            kwargs.pop("highlight", None)
            print(*args, **kwargs)

        def rule(self, title="", **kw):
            print(f"\n{'='*60}\n  {title}\n{'='*60}")

    console = _FallbackConsole()

# ---------------------------------------------------------------------------
# Sample project files (used when no path is supplied)
# ---------------------------------------------------------------------------
SAMPLE_MAIN = textwrap.dedent("""\
    \"\"\"Main application entry-point.\"\"\"

    from utils import calculate_sum, calculate_product
    from models import User


    def greet(name: str) -> str:
        \"\"\"Return a greeting string.\"\"\"
        return f"Hello, {name}!"


    def run_calculations(a: int, b: int) -> dict:
        \"\"\"Run a batch of calculations and return results.\"\"\"
        return {
            "sum": calculate_sum(a, b),
            "product": calculate_product(a, b),
        }


    def create_user(name: str, email: str) -> "User":
        \"\"\"Factory for creating a new User.\"\"\"
        return User(name=name, email=email)


    if __name__ == "__main__":
        print(greet("World"))
        print(run_calculations(3, 7))
        u = create_user("Alice", "alice@example.com")
        print(u)
""")

SAMPLE_UTILS = textwrap.dedent("""\
    \"\"\"Math and string utility helpers.\"\"\"


    def calculate_sum(a: int, b: int) -> int:
        \"\"\"Return the sum of two integers.\"\"\"
        return a + b


    def calculate_product(a: int, b: int) -> int:
        \"\"\"Return the product of two integers.\"\"\"
        return a * b


    def format_currency(amount: float, currency: str = "USD") -> str:
        \"\"\"Format a number as currency.\"\"\"
        symbols = {"USD": "$", "EUR": "€", "GBP": "£"}
        sym = symbols.get(currency, currency)
        return f"{sym}{amount:,.2f}"


    def clamp(value: float, low: float, high: float) -> float:
        \"\"\"Clamp *value* between *low* and *high*.\"\"\"
        return max(low, min(value, high))
""")

SAMPLE_MODELS = textwrap.dedent("""\
    \"\"\"Data models used across the application.\"\"\"
    from dataclasses import dataclass, field
    from typing import List


    @dataclass
    class User:
        \"\"\"Represents an application user.\"\"\"
        name: str
        email: str
        roles: List[str] = field(default_factory=list)

        def add_role(self, role: str) -> None:
            \"\"\"Add a role to the user.\"\"\"
            if role not in self.roles:
                self.roles.append(role)

        def has_role(self, role: str) -> bool:
            \"\"\"Check whether the user has a specific role.\"\"\"
            return role in self.roles


    @dataclass
    class Config:
        \"\"\"Application configuration container.\"\"\"
        debug: bool = False
        log_level: str = "INFO"
        max_retries: int = 3

        def is_verbose(self) -> bool:
            \"\"\"Return True when debug mode or verbose logging is on.\"\"\"
            return self.debug or self.log_level == "DEBUG"
""")

SAMPLE_NEW_FEATURE = textwrap.dedent("""\


    def new_feature(x: int) -> int:
        \"\"\"Newly added feature for incremental indexing demo.\"\"\"
        return x * x + 1
""")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def pp(obj):
    """Pretty-print a dict / list as indented JSON."""
    console.print(json.dumps(obj, indent=2, default=str))


def section(title: str):
    """Print a section divider."""
    if HAS_RICH:
        console.rule(f"[bold cyan]{title}[/bold cyan]")
    else:
        console.rule(title)


def ok(msg: str):
    if HAS_RICH:
        console.print(f"  [green]✓[/green] {msg}")
    else:
        console.print(f"  ✓ {msg}")


def fail(msg: str):
    if HAS_RICH:
        console.print(f"  [red]✗[/red] {msg}")
    else:
        console.print(f"  ✗ {msg}")


def _sanitize_project_path(raw: str) -> Path:
    """Validate and resolve a user-provided project path.

    Rejects path-traversal attempts and ensures the target is an existing directory.
    """
    if ".." in raw or "\x00" in raw:
        console.print("Error: path must not contain '..' or null bytes", style="red")
        sys.exit(1)
    try:
        resolved = Path(os.path.realpath(raw))
    except (OSError, ValueError) as exc:
        console.print(f"Error: invalid path — {exc}", style="red")
        sys.exit(1)
    if not resolved.is_dir():
        console.print(f"Error: {resolved} is not a directory", style="red")
        sys.exit(1)
    return resolved


def _create_sample_project() -> Path:
    """Write the sample project to a temp directory and return the path."""
    tmp = Path(tempfile.mkdtemp(prefix="nexus_demo_"))
    (tmp / "main.py").write_text(SAMPLE_MAIN)
    (tmp / "utils.py").write_text(SAMPLE_UTILS)
    (tmp / "models.py").write_text(SAMPLE_MODELS)
    return tmp


# ---------------------------------------------------------------------------
# Import Nexus-MCP tools — we reach through .fn on the FastMCP wrappers
# ---------------------------------------------------------------------------

def _get_tools():
    """Import and return the raw tool functions from the Nexus-MCP server."""
    # Ensure the package is importable
    project_root = Path(__file__).resolve().parent.parent
    src = project_root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    from nexus_mcp.server import create_server

    mcp = create_server()

    # FastMCP >= 3.x stores tools in _local_provider._components
    # Keys are "tool:{name}", values are FunctionTool with a .fn attribute
    tool_map = {}
    for key, component in mcp._local_provider._components.items():
        if key.startswith("tool:"):
            tool_map[component.name] = component.fn
    return tool_map


# ---------------------------------------------------------------------------
# Demo sequence
# ---------------------------------------------------------------------------

def main():
    if HAS_RICH:
        console.print(
            Panel(
                "[bold]Nexus-MCP Self-Test Demo[/bold]\n"
                "Exercises all 13 MCP tools against a sample project.",
                title="nexus-mcp",
                border_style="blue",
            )
        )
    else:
        console.print("\n=== Nexus-MCP Self-Test Demo ===\n")

    # ---- Resolve project path (sanitized) ----
    if len(sys.argv) > 1:
        project_path = _sanitize_project_path(sys.argv[1])
        cleanup = False
    else:
        project_path = _create_sample_project()
        cleanup = True
        console.print(f"Created sample project at: {project_path}\n")

    try:
        _run_demo(project_path, cleanup)
    finally:
        if cleanup:
            _cleanup_temp(project_path)


def _cleanup_temp(project_path: Path):
    """Remove a temp project directory safely."""
    tmp_root = Path(tempfile.gettempdir()).resolve()
    resolved = project_path.resolve()
    if str(resolved).startswith(str(tmp_root)):
        console.print(f"\nCleaning up temp project: {resolved}")
        shutil.rmtree(resolved, ignore_errors=True)
        ok("Temp files removed")
    else:
        console.print(f"\nSkipping cleanup: {resolved} is not under temp dir")


def _run_demo(project_path: Path, cleanup: bool):
    """Run the full demo sequence."""
    tools = _get_tools()
    passed = 0
    failed = 0

    def run(label, fn, *args, **kwargs):
        nonlocal passed, failed
        try:
            result = fn(*args, **kwargs)
            if isinstance(result, dict) and result.get("error"):
                fail(f"{label}: {result['error']}")
                failed += 1
                return result
            ok(label)
            passed += 1
            return result
        except Exception as exc:
            fail(f"{label}: {exc}")
            failed += 1
            return None

    # ------------------------------------------------------------------
    # 1. Health & Status
    # ------------------------------------------------------------------
    section("1 · Health & Status (pre-index)")

    health_result = run("health()", tools["health"])
    if health_result:
        pp(health_result)

    status_result = run("status()", tools["status"])
    if status_result:
        pp(status_result)

    # ------------------------------------------------------------------
    # 2. Index the project
    # ------------------------------------------------------------------
    section("2 · Index codebase (full)")

    t0 = time.time()
    index_result = run("index(path)", tools["index"], str(project_path))
    elapsed = time.time() - t0
    if index_result:
        pp(index_result)
        console.print(f"\n  Indexing took {elapsed:.2f}s")

    # ------------------------------------------------------------------
    # 3. Status after indexing
    # ------------------------------------------------------------------
    section("3 · Status (post-index)")

    status_result = run("status()", tools["status"])
    if status_result:
        pp(status_result)

    # ------------------------------------------------------------------
    # 4. Hybrid search
    # ------------------------------------------------------------------
    section("4 · Hybrid Search")

    queries = [
        "calculate sum of two numbers",
        "user data model",
        "format currency string",
    ]
    for q in queries:
        console.print(f'\n  Query: "{q}"')
        result = run(f'search("{q}")', tools["search"], q, 5)
        if result and result.get("results"):
            if HAS_RICH:
                tbl = Table(show_header=True, header_style="bold magenta")
                tbl.add_column("#", width=3)
                tbl.add_column("Symbol")
                tbl.add_column("File")
                tbl.add_column("Score", justify="right")
                for i, r in enumerate(result["results"], 1):
                    tbl.add_row(
                        str(i),
                        r.get("symbol_name", r.get("name", "—")),
                        r.get("filepath", "—"),
                        f'{r.get("score", 0):.4f}',
                    )
                console.print(tbl)
            else:
                for i, r in enumerate(result["results"], 1):
                    console.print(
                        f'    {i}. {r.get("symbol_name", "—")} '
                        f'({r.get("filepath", "—")}) '
                        f'score={r.get("score", 0):.4f}'
                    )

    # ------------------------------------------------------------------
    # 5. Vector-only and BM25-only search modes
    # ------------------------------------------------------------------
    section("5 · Search Modes (vector / bm25)")

    for mode in ("vector", "bm25"):
        console.print(f"\n  mode={mode}")
        result = run(
            f'search("clamp value", mode="{mode}")',
            tools["search"],
            "clamp value",
            5,
            "",
            "",
            mode,
            False,
        )
        if result:
            total = result.get("total", 0)
            engines = result.get("engines_used", [])
            console.print(f"    → {total} results via {engines}")

    # ------------------------------------------------------------------
    # 6. Graph tools: find_symbol, find_callers, find_callees
    # ------------------------------------------------------------------
    section("6 · Graph Tools")

    # Note: ast-grep indexes classes and modules into the graph.
    # Use class names that exist in the sample project.
    sym_result = run('find_symbol("User")', tools["find_symbol"], "User")
    if sym_result:
        pp(sym_result)

    console.print()
    callers_result = run('find_callers("User")', tools["find_callers"], "User")
    if callers_result:
        pp(callers_result)

    console.print()
    callees_result = run('find_callees("User")', tools["find_callees"], "User")
    if callees_result:
        pp(callees_result)

    # Fuzzy search
    console.print()
    fuzzy_result = run(
        'find_symbol("con", exact=False)',
        tools["find_symbol"],
        "con",
        False,
    )
    if fuzzy_result:
        names = [s["name"] for s in fuzzy_result.get("symbols", [])]
        console.print(f"  Fuzzy matches: {names}")

    # ------------------------------------------------------------------
    # 7. Analyze
    # ------------------------------------------------------------------
    section("7 · Code Analysis")

    analysis = run("analyze()", tools["analyze"])
    if analysis:
        pp(analysis)

    # ------------------------------------------------------------------
    # 8. Impact analysis
    # ------------------------------------------------------------------
    section("8 · Impact Analysis")

    impact_result = run('impact("User")', tools["impact"], "User")
    if impact_result:
        pp(impact_result)

    # ------------------------------------------------------------------
    # 9. Explain
    # ------------------------------------------------------------------
    section("9 · Explain Symbol")

    for verbosity in ("summary", "detailed"):
        console.print(f"\n  verbosity={verbosity}")
        explain_result = run(
            f'explain("Config", verbosity="{verbosity}")',
            tools["explain"],
            "Config",
            verbosity,
        )
        if explain_result:
            pp(explain_result)

    # ------------------------------------------------------------------
    # 10. Memory: remember → recall → forget
    # ------------------------------------------------------------------
    section("10 · Memory Tools")

    mem1 = run(
        'remember("The auth module needs refactoring", tags="auth,tech-debt")',
        tools["remember"],
        "The auth module needs refactoring",
        "note",
        "auth,tech-debt",
    )
    if mem1:
        pp(mem1)

    mem2 = run(
        'remember("Decided to use LanceDB over ChromaDB", type="decision")',
        tools["remember"],
        "Decided to use LanceDB over ChromaDB for vector storage",
        "decision",
        "architecture",
    )
    if mem2:
        pp(mem2)

    console.print()
    recall_result = run(
        'recall("auth refactoring")',
        tools["recall"],
        "auth refactoring",
        5,
    )
    if recall_result:
        pp(recall_result)

    recall_result2 = run(
        'recall("database choice")',
        tools["recall"],
        "database choice",
        5,
    )
    if recall_result2:
        pp(recall_result2)

    # Forget by tags
    console.print()
    forget_result = run(
        'forget(tags="auth,tech-debt")',
        tools["forget"],
        "",
        "auth,tech-debt",
    )
    if forget_result:
        pp(forget_result)

    # Forget by type
    forget_result2 = run(
        'forget(memory_type="decision")',
        tools["forget"],
        "",
        "",
        "decision",
    )
    if forget_result2:
        pp(forget_result2)

    # ------------------------------------------------------------------
    # 11. Incremental indexing
    # ------------------------------------------------------------------
    section("11 · Incremental Indexing")

    if cleanup:
        # Append new function to main.py
        main_py = project_path / "main.py"
        main_py.write_text(main_py.read_text() + SAMPLE_NEW_FEATURE)
        console.print("  Appended new_feature() to main.py")

        t0 = time.time()
        inc_result = run("index(path) [incremental]", tools["index"], str(project_path))
        elapsed = time.time() - t0
        if inc_result:
            pp(inc_result)
            console.print(f"\n  Incremental indexing took {elapsed:.2f}s")

        # Search for the new function
        console.print()
        new_search = run(
            'search("new_feature square")',
            tools["search"],
            "new_feature square",
            5,
        )
        if new_search and new_search.get("results"):
            console.print(f'  Found {new_search["total"]} results for new_feature')
    else:
        console.print("  (Skipped — using user-provided project, won't modify files)")

    # ------------------------------------------------------------------
    # 12. Final health check
    # ------------------------------------------------------------------
    section("12 · Final Health Check")

    final_health = run("health()", tools["health"])
    if final_health:
        pp(final_health)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    section("Summary")

    total = passed + failed
    if HAS_RICH:
        color = "green" if failed == 0 else "red"
        console.print(
            Panel(
                f"[bold]{passed}/{total} checks passed[/bold]"
                + (f"\n[red]{failed} failed[/red]" if failed else ""),
                title="Results",
                border_style=color,
            )
        )
    else:
        console.print(f"\n  {passed}/{total} checks passed")
        if failed:
            console.print(f"  {failed} FAILED")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
