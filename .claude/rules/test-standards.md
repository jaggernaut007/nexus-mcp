# Test Standards

- Every public function/method must have at least one test
- Use pytest fixtures for shared setup
- No mocking of core data models (Symbol, ParsedFile, etc.)
- Test both happy path and error cases
- Use tmp_path fixture for file system tests
- Tests must be fast (<5s each, <30s total suite)
- Name tests: test_{function_name}_{scenario}

# Post-Phase Checklist

After completing each implementation phase, run the following before moving on:

1. **Code Reviewer agent** — review all code written in the phase for architecture, tests, safety, style
2. **Docs Writer agent** — update PROGRESS.md, CLAUDE.md, README, and add docstrings to public APIs
3. **ADRs** — write Architecture Decision Records for any key decisions made during the phase (docs/adr/)
4. **Research docs** — ensure docs/research/INDEX.md is current; create research notes for any new libraries used
5. **Snyk scan** — run security scan on new/modified code
6. **Tests pass** — `pytest -v` all green, `ruff check .` clean
