"""Tree-sitter symbols for Rust, PHP and C#.

Before 2.2.3 these languages had no tree-sitter config, so a Rust, PHP or C# project
gave no symbols, no embeddings and no `eval` questions.
"""

from nexus_mcp.core.models import SymbolType
from nexus_mcp.parsing.language_registry import TREESITTER_LANGUAGES, get_config_for_language
from nexus_mcp.parsing.treesitter_parser import TreeSitterParser

RUST = """\
//! Geometry helpers for the sample crate.
use std::collections::HashMap;

/// A point on the plane.
#[derive(Debug, Clone)]
pub struct Point { x: i32 }

pub enum Shape { Circle, Square }

pub trait Area {
    fn area(&self) -> f64;
    /// Twice the area.
    fn double(&self) -> f64 { self.area() * 2.0 }
}

impl Point {
    pub fn new(x: i32) -> Self { Point { x } }
}

impl<T> Area for Wrapper<T> { fn area(&self) -> f64 { 0.0 } }

impl std::fmt::Display for Point {
    fn fmt(&self, f: &mut std::fmt::Formatter) -> std::fmt::Result { Ok(()) }
}

// A plain comment is not a doc comment.
pub fn top_level(p: &Point) -> i32 { Point::new(1).x }

const LIMIT: usize = 10;
"""

PHP = """\
<?php
declare(strict_types=1);

/**
 * Log channels for the sample application.
 */

namespace App\\Log;

use App\\Support\\Helper;

interface Greeter { public function greet(string $n): string; }

trait Loud { public function shout() { return strtoupper($this->greet("x")); } }

/**
 * A logger with a name.
 *
 * @author Someone
 */
#[Immutable]
final class Logger implements Greeter {
    /**
     * @param string $name
     */
    public function __construct(private string $name) {}

    /** Adds one record to the log. */
    public function addRecord(): void { Helper::assist(); }

    public function greet(string $n): string { return $n; }
}

enum Level: int { case Debug = 100; public function label() { return 'debug'; } }

function format_name($n) { return trim($n); }
"""

CSHARP = """\
// Request dispatch for the sample service.
using System;

namespace Acme.Shop
{
    /// <summary>
    /// An order with lines.
    /// </summary>
    public class Order : IOrder
    {
        public Order(int total) { }

        /// <summary>Adds an amount to the order.</summary>
        /// <param name="x">The amount.</param>
        [Obsolete]
        public void Add(int x) { Validate(x); }

        private void Validate(int x) { }

        static T Echo<T>(T value) => value;
    }

    public interface IOrder { void Add(int x); }
    public struct Money { public Money Plus() { return this; } }
    public enum Status { Open, Closed }
    public record Customer(string Name);
}
"""


def _parse(tmp_path, name, source):
    path = tmp_path / name
    path.write_text(source)
    return TreeSitterParser().parse_file(str(path))


def _by_name(parsed):
    """Symbols by qualified name. The module symbol is left out: it has the file name."""
    return {s.qualified_name: s for s in parsed.symbols if s.type != SymbolType.MODULE}


def test_treesitter_languages_include_rust_php_csharp():
    assert {"rust", "php", "csharp"} <= TREESITTER_LANGUAGES
    for language in ("rust", "php", "csharp"):
        assert get_config_for_language(language)["doc_comment"] == "leading"


def test_can_parse_rust_php_csharp_files():
    parser = TreeSitterParser()
    for name in ("lib.rs", "Logger.php", "Order.cs"):
        assert parser.can_parse(name)


def test_parse_file_rust_symbols(tmp_path):
    parsed = _parse(tmp_path, "geometry.rs", RUST)
    assert parsed.error is None
    assert parsed.language == "rust"
    symbols = _by_name(parsed)
    for name in ("Point", "Shape", "Area"):
        assert symbols[name].type == SymbolType.CLASS
    assert symbols["top_level"].type == SymbolType.FUNCTION
    assert symbols["LIMIT"].type == SymbolType.VARIABLE
    assert "use std::collections::HashMap;" in parsed.imports


def test_parse_file_rust_impl_methods_have_type_as_parent(tmp_path):
    symbols = _by_name(_parse(tmp_path, "geometry.rs", RUST))
    assert symbols["Point.new"].type == SymbolType.METHOD
    # `impl<T> Area for Wrapper<T>`: the type, not the trait, and no generics.
    assert symbols["Wrapper.area"].parent == "Wrapper"
    # `impl std::fmt::Display for Point`: the last part of a path.
    assert symbols["Point.fmt"].type == SymbolType.METHOD
    # A default method of a trait belongs to the trait.
    assert symbols["Area.double"].type == SymbolType.METHOD
    # An impl block gives no class symbol of its own.
    assert [s for s in symbols.values() if s.name == "Point" and s.type == SymbolType.CLASS]
    assert len([s for s in symbols.values() if s.name == "Point"]) == 1


def test_parse_file_rust_doc_comments(tmp_path):
    symbols = _by_name(_parse(tmp_path, "geometry.rs", RUST))
    # `#[derive]` between the doc comment and the struct is skipped.
    assert symbols["Point"].docstring == "A point on the plane."
    assert symbols["Area.double"].docstring == "Twice the area."
    assert symbols["top_level"].docstring == ""  # `//` is not a doc comment


def test_parse_file_rust_module_doc_from_inner_comment(tmp_path):
    symbols = _parse(tmp_path, "geometry.rs", RUST).symbols
    module = [s for s in symbols if s.type == SymbolType.MODULE]
    assert module and "Geometry helpers" in module[0].code_snippet


def test_parse_file_php_symbols(tmp_path):
    parsed = _parse(tmp_path, "Logger.php", PHP)
    assert parsed.error is None
    symbols = _by_name(parsed)
    for name in ("Greeter", "Loud", "Logger", "Level"):
        assert symbols[name].type == SymbolType.CLASS
    assert symbols["Logger.addRecord"].type == SymbolType.METHOD
    assert symbols["Loud.shout"].parent == "Loud"
    assert symbols["Level.label"].parent == "Level"
    assert symbols["format_name"].type == SymbolType.FUNCTION
    assert any("App\\Support\\Helper" in imp for imp in parsed.imports)


def test_parse_file_php_docblocks(tmp_path):
    symbols = _by_name(_parse(tmp_path, "Logger.php", PHP))
    # An attribute between the docblock and the class is skipped; `@author` is a tag.
    assert symbols["Logger"].docstring == "A logger with a name."
    assert symbols["Logger.addRecord"].docstring == "Adds one record to the log."
    assert symbols["Logger.__construct"].docstring == ""  # only `@param` tags


def test_parse_file_php_module_doc_after_php_tag(tmp_path):
    symbols = _parse(tmp_path, "Logger.php", PHP).symbols
    module = [s for s in symbols if s.type == SymbolType.MODULE]
    assert module and "Log channels" in module[0].code_snippet


def test_parse_file_csharp_symbols(tmp_path):
    parsed = _parse(tmp_path, "Order.cs", CSHARP)
    assert parsed.error is None
    assert parsed.language == "csharp"
    symbols = _by_name(parsed)
    for name in ("Order", "IOrder", "Money", "Status", "Customer"):
        assert symbols[name].type == SymbolType.CLASS
    assert symbols["Order.Order"].type == SymbolType.METHOD  # constructor
    assert symbols["Order.Add"].type == SymbolType.METHOD
    assert symbols["Money.Plus"].parent == "Money"
    assert "using System;" in parsed.imports


def test_parse_file_csharp_generic_method_named_by_its_name(tmp_path):
    symbols = _by_name(_parse(tmp_path, "Order.cs", CSHARP))
    # `static T Echo<T>(T value)`: the return type `T` is not the name.
    assert "Order.Echo" in symbols
    assert "Order.T" not in symbols


def test_parse_file_csharp_xml_doc_comments(tmp_path):
    symbols = _by_name(_parse(tmp_path, "Order.cs", CSHARP))
    assert symbols["Order"].docstring == "An order with lines."
    # The `[Obsolete]` attribute sits between the comment and the method.
    assert symbols["Order.Add"].docstring == "Adds an amount to the order."
    assert symbols["Order.Validate"].docstring == ""
