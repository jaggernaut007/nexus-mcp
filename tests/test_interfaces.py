"""Tests for core/interfaces.py."""

from nexus_mcp.core.interfaces import IEngine, IParser
from nexus_mcp.core.models import ParsedFile


class ConcreteParser(IParser):
    def can_parse(self, filepath: str) -> bool:
        return filepath.endswith(".py")

    def parse_file(self, filepath: str) -> ParsedFile:
        return ParsedFile(filepath=filepath, language="python")

    def get_supported_extensions(self):
        return [".py"]


class ConcreteEngine(IEngine):
    def __init__(self):
        self._items = {}

    def add(self, items):
        for item in items:
            self._items[item.get("id", str(len(self._items)))] = item

    def search(self, query, limit=10, **kwargs):
        return list(self._items.values())[:limit]

    def delete(self, ids):
        for i in ids:
            self._items.pop(i, None)

    def count(self):
        return len(self._items)


def test_parser_interface():
    parser = ConcreteParser()
    assert parser.can_parse("test.py")
    assert not parser.can_parse("test.js")
    result = parser.parse_file("test.py")
    assert result.language == "python"
    assert ".py" in parser.get_supported_extensions()


def test_engine_interface():
    engine = ConcreteEngine()
    engine.add([{"id": "1", "text": "hello"}, {"id": "2", "text": "world"}])
    assert engine.count() == 2
    results = engine.search("hello", limit=1)
    assert len(results) == 1
    engine.delete(["1"])
    assert engine.count() == 1
