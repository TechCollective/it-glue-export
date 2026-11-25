from unittest.mock import MagicMock, patch

import pytest
import requests

from itglue_export.storage import s3
from itglue_export.storage.s3 import S3Storage


def _response(chunks):
    r = MagicMock()
    r.iter_content.return_value = chunks
    return r


@pytest.fixture
def storage(config, monkeypatch):
    monkeypatch.setenv("S3_BUCKET", "backups")
    monkeypatch.setenv("S3_PREFIX", "itglue/")
    config.s3_bucket, config.s3_prefix = "backups", "itglue/"
    monkeypatch.setattr(s3, "PART_SIZE", 10)
    client = MagicMock()
    client.create_multipart_upload.return_value = {"UploadId": "u1"}
    client.upload_part.side_effect = lambda **kw: {"ETag": f'"etag{kw["PartNumber"]}"'}
    return S3Storage(config, client=client), client


def test_streams_in_fixed_size_parts(storage):
    store, client = storage
    with patch("itglue_export.storage.s3.requests.get", return_value=_response([b"abcd", b"efgh", b"ijkl", b"mnop", b"qr"])):
        uri = store.store_stream("https://dl.example.test/x.zip", "itglue-backup-1.zip")

    assert uri == "s3://backups/itglue/itglue-backup-1.zip"
    bodies = [c.kwargs["Body"] for c in client.upload_part.call_args_list]
    assert bodies == [b"abcdefghij", b"klmnopqr"]
    client.complete_multipart_upload.assert_called_once_with(
        Bucket="backups", Key="itglue/itglue-backup-1.zip", UploadId="u1",
        MultipartUpload={"Parts": [{"PartNumber": 1, "ETag": '"etag1"'}, {"PartNumber": 2, "ETag": '"etag2"'}]},
    )
    client.abort_multipart_upload.assert_not_called()


def test_mid_stream_failure_aborts_upload(storage):
    store, client = storage
    client.upload_part.side_effect = [{"ETag": '"e1"'}, RuntimeError("network")]
    with patch("itglue_export.storage.s3.requests.get", return_value=_response([b"0123456789", b"0123456789", b"x"])):
        with pytest.raises(RuntimeError):
            store.store_stream("https://dl.example.test/x.zip", "a.zip")
    client.abort_multipart_upload.assert_called_once_with(Bucket="backups", Key="itglue/a.zip", UploadId="u1")
    client.complete_multipart_upload.assert_not_called()


def test_config_selects_s3_without_gdrive_settings(monkeypatch):
    from itglue_export.config import Configuration

    monkeypatch.setenv("ITGLUE_API_KEY", "k")
    monkeypatch.setenv("S3_BUCKET", "backups")
    monkeypatch.delenv("GDRIVE_FOLDER_ID", raising=False)
    Configuration().validate()  # no GDRIVE_* required when S3 is the backend


def test_stream_errors_drop_the_signed_url(storage):
    store, client = storage
    signed = "https://itg-prod.s3.amazonaws.com/x.zip?X-Amz-Signature=deadbeef"
    r = _response([])
    r.raise_for_status.side_effect = requests.HTTPError(f"403 Forbidden for url: {signed}")
    with patch("itglue_export.storage.s3.requests.get", return_value=r):
        with pytest.raises(requests.HTTPError) as exc:
            store.store_stream(signed, "a.zip")
    assert "X-Amz" not in str(exc.value)
