from pathlib import Path


def test_python_runtime_is_pinned_to_supported_version():
    repo_root = Path(__file__).resolve().parents[1]
    pyproject_text = (repo_root / "pyproject.toml").read_text()
    glama_text = (repo_root / "glama.json").read_text()

    assert 'requires-python = ">=3.10,<3.13"' in pyproject_text

    python_version = (repo_root / ".python-version").read_text().strip()
    assert python_version == "3.12"
    assert '"pythonVersion": "3.12"' in glama_text
