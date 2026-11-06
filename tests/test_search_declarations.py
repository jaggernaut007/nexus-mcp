"""search drops a one-line declaration when the definition of the same name is in the results."""

from nexus_mcp.core_api import _drop_declarations_of_defined_names


def _r(name, path, start, end):
    return {"symbol_name": name, "filepath": path, "line_start": start, "line_end": end}


def test_drop_declarations_of_defined_names_removes_a_prototype_next_to_its_body():
    results = [
        _r("removeExpire", "src/db.c", 2878, 2890),
        _r("removeExpire", "src/server.h", 4495, 4495),
        _r("keyIsExpired", "src/db.c", 3079, 3088),
    ]
    kept = _drop_declarations_of_defined_names(results)
    assert [(r["symbol_name"], r["filepath"]) for r in kept] == [
        ("removeExpire", "src/db.c"), ("keyIsExpired", "src/db.c"),
    ]


def test_drop_declarations_of_defined_names_keeps_a_prototype_without_a_body():
    results = [_r("dictFind", "src/dict.h", 200, 200), _r("other", "src/a.c", 1, 9)]
    assert _drop_declarations_of_defined_names(results) == results


def test_drop_declarations_of_defined_names_keeps_a_one_line_function_of_another_name():
    results = [_r("f", "a.py", 3, 3), _r("g", "a.py", 5, 9)]
    assert _drop_declarations_of_defined_names(results) == results
