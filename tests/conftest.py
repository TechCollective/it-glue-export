import pytest

from itglue_export.config import Configuration


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch):
    """Keep the repo's real .env out of the tests."""
    monkeypatch.setattr("itglue_export.config.load_dotenv", lambda *a, **k: None)


@pytest.fixture
def config(monkeypatch, tmp_path):
    monkeypatch.setenv("ITGLUE_API_KEY", "test-key")
    monkeypatch.setenv("ITGLUE_API_ENDPOINT", "https://api.example.test")
    monkeypatch.setenv("LOCAL_TEMP_DIR", str(tmp_path))
    return Configuration(local=True)
