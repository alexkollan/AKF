import sys
from pathlib import Path

import pytest

# The repo root is the import root (`[tool.uv] package = false`), so make sure it is on
# sys.path even when pytest is invoked from somewhere else.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def isolated_config_dir(tmp_path, monkeypatch):
    """Never read or write the real %APPDATA%/fishbot during tests."""
    monkeypatch.setenv("FISHBOT_CONFIG_DIR", str(tmp_path / "fishbot"))
    return tmp_path / "fishbot"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
