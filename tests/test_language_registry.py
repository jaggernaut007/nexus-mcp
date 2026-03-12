"""Tests for parsing/language_registry.py."""

import pytest

from nexus_mcp.parsing.language_registry import (
    EXTENSION_MAP,
    LANGUAGE_CONFIGS,
    get_all_languages,
    get_astgrep_extensions,
    get_config_for_language,
    get_language_for_file,
    get_supported_extensions,
    get_treesitter_extensions,
    validate_config,
)


def test_get_language_for_file_python():
    assert get_language_for_file("test.py") == "python"
    assert get_language_for_file("/path/to/file.pyi") == "python"


def test_get_language_for_file_javascript():
    assert get_language_for_file("app.js") == "javascript"
    assert get_language_for_file("app.jsx") == "javascript"


def test_get_language_for_file_typescript():
    assert get_language_for_file("app.ts") == "typescript"
    assert get_language_for_file("app.tsx") == "typescript"


def test_get_language_for_file_unsupported():
    assert get_language_for_file("readme.txt") is None
    assert get_language_for_file("data.csv") is None


def test_get_config_for_language():
    config = get_config_for_language("python")
    assert "function_types" in config
    assert "function_definition" in config["function_types"]


def test_get_config_unsupported_language():
    with pytest.raises(KeyError, match="Unsupported language"):
        get_config_for_language("brainfuck")


def test_get_supported_extensions():
    exts = get_supported_extensions()
    assert ".py" in exts
    assert ".js" in exts
    assert ".rs" in exts  # ast-grep only


def test_get_treesitter_extensions():
    exts = get_treesitter_extensions()
    assert ".py" in exts
    assert ".go" in exts
    # Rust is ast-grep only, not tree-sitter
    assert ".rs" not in exts


def test_get_astgrep_extensions():
    exts = get_astgrep_extensions()
    assert ".py" in exts
    assert ".rs" in exts


def test_get_all_languages():
    langs = get_all_languages()
    assert "python" in langs
    assert "rust" in langs
    assert len(langs) > 10


def test_validate_config_valid():
    assert validate_config("python") is True
    assert validate_config("javascript") is True


def test_validate_config_invalid():
    with pytest.raises(KeyError):
        validate_config("nonexistent")


def test_all_configs_have_required_fields():
    for lang in LANGUAGE_CONFIGS:
        config = LANGUAGE_CONFIGS[lang]
        assert "function_types" in config
        assert "class_types" in config
        assert "import_types" in config
        assert "body_field" in config


def test_extension_map_completeness():
    # Every language in LANGUAGE_CONFIGS should have at least one extension
    languages_with_exts = set(EXTENSION_MAP.values())
    for lang in LANGUAGE_CONFIGS:
        assert lang in languages_with_exts, f"{lang} has no extensions"
