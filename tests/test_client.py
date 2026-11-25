from unittest.mock import MagicMock, patch

import pytest
import requests

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


def test_check_export_returns_url_or_none(config):
    pending = _response(200, {"data": {"attributes": {"status": "processing"}}})
    done = _response(200, {"data": {"attributes": {"download-url": "https://dl.example.test/x.zip"}}})
    with patch("itglue_export.client.requests.get", side_effect=[pending, done]) as get:
        client = ITGlueClient(config)
        assert client.check_export("42") is None
        assert client.check_export("42") == "https://dl.example.test/x.zip"
    assert get.call_count == 2
    assert get.call_args.args[0].endswith("/exports/42")


@pytest.mark.parametrize("status", [401, 429, 500])
def test_check_export_raises_on_non_200(config, status):
    with patch("itglue_export.client.requests.get", return_value=_response(status)), \
         patch("itglue_export.client.time.sleep") as sleep:
        with pytest.raises(requests.HTTPError) as exc:
            ITGlueClient(config).check_export("42")
    assert exc.value.response.status_code == status
    sleep.assert_not_called()


def test_poll_sleeps_longer_on_429(config):
    limited = _response(429)
    done = _response(200, {"data": {"attributes": {"download-url": "https://dl.example.test/x.zip"}}})
    with patch("itglue_export.client.requests.get", side_effect=[limited, done]), \
         patch("itglue_export.client.time.sleep") as sleep:
        assert ITGlueClient(config).poll_export_status("42", poll_interval_sec=5) == "https://dl.example.test/x.zip"
    sleep.assert_called_once_with(10)


def test_poll_raises_on_other_errors(config):
    with patch("itglue_export.client.requests.get", return_value=_response(500)), \
         patch("itglue_export.client.time.sleep"):
        with pytest.raises(requests.HTTPError):
            ITGlueClient(config).poll_export_status("42", poll_interval_sec=1)


def test_download_streams_to_file(config, tmp_path):
    r = _response(200)
    r.headers = {"content-length": "6"}
    r.iter_content.return_value = [b"abc", b"", b"def"]
    out = tmp_path / "sub" / "x.zip"
    with patch("itglue_export.client.requests.get", return_value=r):
        ITGlueClient(config).download_export("https://dl.example.test/x.zip", out)
    assert out.read_bytes() == b"abcdef"


SIGNED_URL = "https://itg-prod.s3.amazonaws.com/exports/x.zip?X-Amz-Credential=AKIAEXAMPLE&X-Amz-Signature=deadbeef"


def test_download_logs_host_only(config, tmp_path, caplog):
    r = _response(200)
    r.headers = {}
    r.iter_content.return_value = [b"abc"]
    with patch("itglue_export.client.requests.get", return_value=r), caplog.at_level("INFO"):
        ITGlueClient(config).download_export(SIGNED_URL, tmp_path / "x.zip")
    assert "itg-prod.s3.amazonaws.com" in caplog.text
    assert "X-Amz" not in caplog.text and "/exports/x.zip" not in caplog.text


@pytest.mark.parametrize("error", [
    requests.HTTPError(f"403 Client Error: Forbidden for url: {SIGNED_URL}", response=_response(403)),
    requests.ConnectionError(f"Max retries exceeded with url: {SIGNED_URL}"),
    requests.exceptions.ChunkedEncodingError(f"Connection broken while reading {SIGNED_URL}"),
])
def test_download_errors_drop_the_signed_url(config, tmp_path, error):
    r = _response(200)
    r.headers = {}
    r.iter_content.side_effect = error
    with patch("itglue_export.client.requests.get", return_value=r):
        with pytest.raises(type(error)) as exc:
            ITGlueClient(config).download_export(SIGNED_URL, tmp_path / "x.zip")
    assert "X-Amz" not in str(exc.value) and "itg-prod.s3.amazonaws.com" in str(exc.value)
    assert exc.value.__suppress_context__
