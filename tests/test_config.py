from itglue_export.config import Configuration


def test_passwords_excluded_by_default(monkeypatch, tmp_path):
    monkeypatch.delenv("ITGLUE_INCLUDE_PASSWORDS", raising=False)
    assert Configuration(local=True).include_passwords is False


def test_passwords_included_when_set(monkeypatch, tmp_path):
    monkeypatch.setenv("ITGLUE_INCLUDE_PASSWORDS", "true")
    assert Configuration(local=True).include_passwords is True
