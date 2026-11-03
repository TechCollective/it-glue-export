from unittest.mock import MagicMock, patch

import pytest

from itglue_export.client import ITGlueClient


def _response(status=200, json=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json or {}
    r.text = text
    return r


def test_trigger_returns_export_id(config):
    with patch("itglue_export.client.requests.post", return_value=_response(201, {"data": {"id": 42}})) as post:
        assert ITGlueClient(config).trigger_export() == "42"
    attrs = post.call_args.kwargs["json"]["data"]["attributes"]
    assert attrs == {"include-logs": False, "include-passwords": False}


def test_trigger_422_reuses_latest_matching_export(config):
    conflict = _response(422, {"errors": [{"detail": "An export is already available"}]})
    listing = _response(200, {"data": [
        {"id": "1", "attributes": {"created-at": "2026-01-01"}},
        {"id": "2", "attributes": {"created-at": "2026-02-01"}},
        {"id": "3", "attributes": {"created-at": "2026-03-01", "organization-id": 99}},
    ]})
    with patch("itglue_export.client.requests.post", return_value=conflict), \
         patch("itglue_export.client.requests.get", return_value=listing):
        assert ITGlueClient(config).trigger_export() == "2"


def test_poll_times_out(config):
    clock = iter(range(0, 10_000, 100))
    with patch("itglue_export.client.requests.get", return_value=_response(200, {"data": {"attributes": {}}})), \
         patch("itglue_export.client.time.sleep"), \
         patch("itglue_export.client.time.time", side_effect=lambda: next(clock)):
        with pytest.raises(TimeoutError):
            ITGlueClient(config).poll_export_status("42", poll_interval_sec=1, timeout_min=1)


def test_poll_returns_download_url(config):
    pending = _response(200, {"data": {"attributes": {"status": "processing"}}})
    done = _response(200, {"data": {"attributes": {"download-url": "https://dl.example.test/x.zip"}}})
    with patch("itglue_export.client.requests.get", side_effect=[pending, done]), \
         patch("itglue_export.client.time.sleep"):
        assert ITGlueClient(config).poll_export_status("42", poll_interval_sec=1) == "https://dl.example.test/x.zip"


def test_download_streams_to_file(config, tmp_path):
    r = _response(200)
    r.headers = {"content-length": "6"}
    r.iter_content.return_value = [b"abc", b"", b"def"]
    out = tmp_path / "sub" / "x.zip"
    with patch("itglue_export.client.requests.get", return_value=r):
        ITGlueClient(config).download_export("https://dl.example.test/x.zip", out)
    assert out.read_bytes() == b"abcdef"
