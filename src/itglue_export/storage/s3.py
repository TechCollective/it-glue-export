import logging
from collections.abc import Iterator

import requests

from ..client import redacted_download_errors
from ..config import Configuration

logger = logging.getLogger(__name__)

PART_SIZE = 64 * 1024 * 1024  # S3 needs every part but the last to be >= 5 MB; 64 MB keeps part counts low


def _parts(response: requests.Response, part_size: int) -> Iterator[bytes]:
    """Regroup the HTTP stream into parts of exactly part_size bytes (the last one may be shorter)."""
    buf = bytearray()
    for chunk in response.iter_content(chunk_size=1024 * 1024):
        buf += chunk
        while len(buf) >= part_size:
            yield bytes(buf[:part_size])
            del buf[:part_size]
    if buf:
        yield bytes(buf)


class S3Storage:
    """Stream the export from its presigned URL into S3 as a multipart upload (needs the [s3] extra)."""

    def __init__(self, config: Configuration, client=None):
        if client is None:
            try:
                import boto3
            except ImportError as e:
                raise ImportError("S3 upload needs the [s3] extra: uv sync --extra s3") from e
            client = boto3.client("s3")
        self.client = client
        self.bucket = config.s3_bucket
        self.prefix = config.s3_prefix

    def store_stream(self, download_url: str, filename: str) -> str:
        key = f"{self.prefix}{filename}"
        logger.info(f"Streaming export into s3://{self.bucket}/{key} ...")
        # Stream errors surface while iterating, so the redaction covers the upload loop too.
        with redacted_download_errors(download_url):
            response = requests.get(download_url, stream=True, timeout=120)
            response.raise_for_status()

            upload_id = self.client.create_multipart_upload(Bucket=self.bucket, Key=key)["UploadId"]
            parts = []
            total = 0
            try:
                for number, body in enumerate(_parts(response, PART_SIZE), start=1):
                    etag = self.client.upload_part(
                        Bucket=self.bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
                    )["ETag"]
                    parts.append({"PartNumber": number, "ETag": etag})
                    total += len(body)
                    logger.info(f"Uploaded part {number} ({total / 1048576:.1f} MB so far)")
                self.client.complete_multipart_upload(
                    Bucket=self.bucket, Key=key, UploadId=upload_id, MultipartUpload={"Parts": parts}
                )
            except Exception:
                logger.error(f"Upload failed after {len(parts)} parts; aborting multipart upload {upload_id}")
                self.client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
                raise

        uri = f"s3://{self.bucket}/{key}"
        logger.info(f"Upload complete: {uri} ({total / 1048576:.1f} MB, {len(parts)} parts)")
        return uri
