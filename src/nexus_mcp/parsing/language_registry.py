"""Merged language registry for Nexus-MCP.

Combines CodeGrok's tree-sitter language_configs with code-graph-mcp's
ast-grep LanguageRegistry. Supports 25+ languages.
"""

from pathlib import Path
from typing import Dict, List, Optional, Set

# File extension → language name mapping
EXTENSION_MAP: Dict[str, str] = {
    # Python
    ".py": "python",
    ".pyi": "python",
    ".pyw": "python",
    # JavaScript
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    # TypeScript
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    # C
    ".c": "c",
    ".h": "c",
    # C++
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c++": "cpp",
    ".hpp": "cpp",
    ".hh": "cpp",
    ".hxx": "cpp",
    ".h++": "cpp",
    # Bash
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    # Go
    ".go": "go",
    # Java
    ".java": "java",
    # Kotlin
    ".kt": "kotlin",
    ".kts": "kotlin",
    # Rust (ast-grep only)
    ".rs": "rust",
    # Ruby (ast-grep only)
    ".rb": "ruby",
    ".erb": "ruby",
    # PHP (ast-grep only)
    ".php": "php",
    # Swift (ast-grep only)
    ".swift": "swift",
    # C# (ast-grep only)
    ".cs": "csharp",
    # Scala (ast-grep only)
    ".scala": "scala",
    # Lua (ast-grep only)
    ".lua": "lua",
    # Dart (ast-grep only)
    ".dart": "dart",
}

# Languages supported by tree-sitter parser (symbol extraction)
TREESITTER_LANGUAGES = {
    "python", "javascript", "typescript", "c", "cpp",
    "bash", "go", "java", "kotlin",
}

# Languages supported by ast-grep parser (structural analysis)
ASTGREP_LANGUAGES = {
    "python", "javascript", "typescript", "c", "cpp",
    "go", "java", "kotlin", "rust", "ruby", "php",
    "swift", "csharp", "scala", "lua", "dart",
}

# Tree-sitter AST node type configurations per language
LANGUAGE_CONFIGS: Dict[str, Dict[str, list]] = {
    "python": {
        "function_types": ["function_definition"],
        "class_types": ["class_definition"],
        "method_types": ["function_definition"],
        "constant_types": ["expression_statement"],
        "import_types": ["import_statement", "import_from_statement"],
        "call_types": ["call"],
        "docstring_field": "string",
        "identifier_field": "name",
        "body_field": "body",
    },
    "javascript": {
        "function_types": ["function_declaration", "function", "generator_function_declaration"],
        "class_types": ["class_declaration"],
        "method_types": ["method_definition", "function_expression", "arrow_function"],
        "constant_types": ["lexical_declaration"],
        "import_types": ["import_statement", "import_clause"],
        "call_types": ["call_expression", "new_expression"],
        "docstring_field": "comment",
        "identifier_field": "name",
        "body_field": "body",
    },
    "typescript": {
        "function_types": [
            "function_declaration", "function_signature", "generator_function_declaration",
        ],
        "class_types": [
            "class_declaration", "interface_declaration", "type_alias_declaration",
        ],
        "method_types": [
            "method_definition", "method_signature", "arrow_function", "function_expression",
        ],
        "constant_types": [],
        "import_types": ["import_statement", "import_clause"],
        "call_types": ["call_expression", "new_expression"],
        "docstring_field": "comment",
        "identifier_field": "name",
        "body_field": "body",
    },
    "c": {
        "function_types": ["function_definition", "function_declarator"],
        "class_types": ["struct_specifier", "union_specifier"],
        "method_types": ["function_definition"],
        "constant_types": [],
        "import_types": ["preproc_include"],
        "call_types": ["call_expression"],
        "docstring_field": "comment",
        "identifier_field": "declarator",
        "body_field": "body",
    },
    "cpp": {
        "function_types": ["function_definition"],
        "class_types": ["class_specifier", "struct_specifier", "union_specifier"],
        "method_types": ["function_definition"],
        "constant_types": [],
        "import_types": ["preproc_include"],
        "call_types": ["call_expression"],
        "docstring_field": "comment",
        "identifier_field": "declarator",
        "body_field": "body",
    },
    "bash": {
        "function_types": ["function_definition"],
        "class_types": [],
        "method_types": [],
        "constant_types": ["declaration_command"],
        "import_types": ["command"],
        "call_types": ["command", "command_substitution"],
        "docstring_field": "comment",
        "identifier_field": "name",
        "body_field": "body",
    },
    "go": {
        "function_types": ["function_declaration"],
        "class_types": ["type_declaration"],
        "method_types": ["method_declaration"],
        "constant_types": ["const_declaration"],
        "import_types": ["import_declaration", "import_spec"],
        "call_types": ["call_expression"],
        "docstring_field": "comment",
        "identifier_field": "name",
        "body_field": "body",
    },
    "java": {
        "function_types": ["method_declaration"],
        "class_types": ["class_declaration", "interface_declaration", "enum_declaration"],
        "method_types": ["method_declaration", "constructor_declaration"],
        "constant_types": ["field_declaration"],
        "import_types": ["import_declaration"],
        "call_types": ["method_invocation", "object_creation_expression"],
        "docstring_field": "comment",
        "identifier_field": "name",
        "body_field": "body",
    },
    "kotlin": {
        "function_types": ["function_declaration"],
        "class_types": ["class_declaration", "object_declaration"],
        "method_types": ["function_declaration"],
        "constant_types": ["property_declaration"],
        "import_types": ["import_header"],
        "call_types": ["call_expression"],
        "docstring_field": "comment",
        "identifier_field": "simple_identifier",
        "body_field": "class_body",
    },
}

# Validate all configs on import
_REQUIRED_FIELDS = [
    "function_types", "class_types", "method_types",
    "import_types", "call_types", "docstring_field",
    "identifier_field", "body_field",
]
for _lang, _config in LANGUAGE_CONFIGS.items():
    _missing = [f for f in _REQUIRED_FIELDS if f not in _config]
    if _missing:
        raise ValueError(f"Config for {_lang} missing: {', '.join(_missing)}")


def get_language_for_file(filepath: str) -> Optional[str]:
    """Get language name from file path, or None if unsupported."""
    extension = Path(filepath).suffix.lower()
    return EXTENSION_MAP.get(extension)


def get_config_for_language(language: str) -> Dict[str, list]:
    """Get tree-sitter config for a language. Raises KeyError if unsupported."""
    if language not in LANGUAGE_CONFIGS:
        raise KeyError(
            f"Unsupported language: {language}. "
            f"Supported: {', '.join(LANGUAGE_CONFIGS.keys())}"
        )
    return LANGUAGE_CONFIGS[language]


def get_supported_extensions() -> Set[str]:
    """Get all supported file extensions."""
    return set(EXTENSION_MAP.keys())


def get_treesitter_extensions() -> Set[str]:
    """Get extensions supported by tree-sitter parser."""
    return {ext for ext, lang in EXTENSION_MAP.items() if lang in TREESITTER_LANGUAGES}


def get_astgrep_extensions() -> Set[str]:
    """Get extensions supported by ast-grep parser."""
    return {ext for ext, lang in EXTENSION_MAP.items() if lang in ASTGREP_LANGUAGES}


def get_all_languages() -> List[str]:
    """Get all supported language names."""
    return sorted(set(EXTENSION_MAP.values()))


def validate_config(language: str) -> bool:
    """Validate a language config has all required fields."""
    config = get_config_for_language(language)
    missing = [f for f in _REQUIRED_FIELDS if f not in config]
    if missing:
        raise ValueError(f"Config for {language} missing: {', '.join(missing)}")
    return True
