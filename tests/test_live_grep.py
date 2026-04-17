import os
import pytest
import shutil
from pathlib import Path
from nexus_mcp.engines.live_grep import LiveGrepEngine

@pytest.fixture
def temp_workspace(tmp_path):
    """Create a temporary workspace with some files."""
    (tmp_path / "file1.py").write_text("def hello_world():\n    print('hello')\n")
    (tmp_path / "file2.txt").write_text("This is a secret message.\nKeep it safe.\n")
    return tmp_path

def test_live_grep_rg(temp_workspace):
    """Test ripgrep search if installed."""
    if not shutil.which("rg"):
        pytest.skip("ripgrep (rg) not installed")
    
    engine = LiveGrepEngine(str(temp_workspace))
    results = engine.search("hello")
    
    assert len(results) >= 1
    assert any("file1.py" in r["absolute_path"] for r in results)
    assert any("hello_world" in r["code_snippet"] for r in results)
    assert results[0]["search_mode"] == "live_grep_rg"

def test_live_grep_grep(temp_workspace):
    """Test grep search fallback."""
    if not shutil.which("grep"):
        pytest.skip("grep not installed")
    
    engine = LiveGrepEngine(str(temp_workspace))
    # Force grep by setting rg_path to None
    engine.rg_path = None
    
    results = engine.search("secret")
    
    assert len(results) >= 1
    assert any("file2.txt" in r["absolute_path"] for r in results)
    assert any("secret message" in r["code_snippet"] for r in results)
    assert results[0]["search_mode"] == "live_grep_grep"

def test_live_grep_limit(temp_workspace):
    """Test search result limit."""
    (temp_workspace / "multi.py").write_text("\n".join([f"line {i}" for i in range(100)]))
    
    engine = LiveGrepEngine(str(temp_workspace))
    results = engine.search("line", limit=5)
    
    assert len(results) == 5

def test_live_grep_empty_query(temp_workspace):
    """Test empty query handling."""
    engine = LiveGrepEngine(str(temp_workspace))
    assert engine.search("") == []
    assert engine.search("   ") == []
