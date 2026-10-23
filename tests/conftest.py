import pytest

from app import config, db
from app.data import seed


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    """No real LLM calls and no files outside tmp in tests."""
    monkeypatch.setattr(config, "LLM_API_KEY", "")
    monkeypatch.setattr(config, "OUT_DIR", tmp_path / "out")


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    seed.seed(c)
    yield c
    c.close()
