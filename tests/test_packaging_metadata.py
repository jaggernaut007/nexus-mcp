import json
from pathlib import Path

import tomllib
from packaging.specifiers import SpecifierSet
from packaging.version import Version

# The interpreter the Glama build must resolve to. glama.json (pythonVersion)
# and .python-version pin this; requires-python must also permit it.
PINNED_PYTHON = "3.12"


def test_python_runtime_is_pinned_to_supported_version():
    repo_root = Path(__file__).resolve().parents[1]

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    requires_python = pyproject["project"]["requires-python"]
    assert Version(PINNED_PYTHON) in SpecifierSet(requires_python)

    assert (repo_root / ".python-version").read_text().strip() == PINNED_PYTHON

    glama = json.loads((repo_root / "glama.json").read_text())
    assert glama["pythonVersion"] == PINNED_PYTHON
