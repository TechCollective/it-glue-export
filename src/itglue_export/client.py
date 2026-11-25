import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

from .config import Configuration

logger = logging.getLogger(__name__)


@contextmanager
def redacted_download_errors(download_url: str) -> Iterator[None]:
    """Re-raise requests errors without the URL: its query string is a presigned credential for the backup."""
    try:
        yield
    except requests.RequestException as e:
        detail = f"HTTP {e.response.status_code}" if e.response is not None else type(e).__name__
        raise type(e)(f"Download from {urlsplit(download_url).netloc} failed: {detail}", response=e.response) from None



class ITGlueClient:
    """Trigger, reuse, poll and download exports through the IT Glue Exports API."""

    def __init__(self, config: Configuration):
        self.config = config
        self.headers = {
            "x-api-key": config.api_key,
            "Content-Type": "application/vnd.api+json",
        }

    def trigger_export(self) -> str:
        """Start an export job and return its ID, reusing a matching existing export on 422."""
        url = f"{self.config.api_endpoint}/exports"
        attributes: dict[str, Any] = {
            "include-logs": self.config.include_logs,
            "include-passwords": self.config.include_passwords,
        }
        if self.config.org_id:
            attributes["organization-id"] = self.config.org_id
        if self.config.zip_password:
            attributes["zip-password"] = self.config.zip_password
        payload = {"data": {"type": "exports", "attributes": attributes}}

        logger.info("Triggering IT Glue export job...")
        response = requests.post(url, json=payload, headers=self.headers, timeout=30)

        if response.status_code == 429:
            logger.error("Rate limit hit. IT Glue returned 429 Too Many Requests.")
            response.raise_for_status()
        elif response.status_code == 422:
            detail = ""
            try:
                detail = response.json().get("errors", [{}])[0].get("detail", "")
            except Exception:
                pass
            if "already available" in detail:
                logger.info("An export with the same attributes is already available. Fetching the existing export...")
                return self.get_latest_matching_export()
            logger.error(f"Failed to trigger export. Status: {response.status_code}, Body: {response.text}")
            response.raise_for_status()
        elif response.status_code not in (200, 201, 202):
            logger.error(f"Failed to trigger export. Status: {response.status_code}, Body: {response.text}")
            response.raise_for_status()

        data = response.json()
        try:
            export_id = data["data"]["id"]
        except KeyError as e:
            logger.error(f"Failed to parse export ID from response: {data}")
            raise KeyError("Invalid response structure from IT Glue API") from e
        logger.info(f"Export job triggered successfully. Export ID: {export_id}")
        return str(export_id)

    def get_latest_matching_export(self) -> str:
        """Return the ID of the newest existing export for the configured organization."""
        url = f"{self.config.api_endpoint}/exports"
        response = requests.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()

        matching = []
        for export in response.json().get("data", []):
            attrs = export.get("attributes", {})
            org_id = attrs.get("organization-id") or attrs.get("organization_id")
            if org_id == self.config.org_id:
                matching.append(export)
        if not matching:
            raise ValueError("No matching exports found despite 422 error.")

        def sort_key(exp):
            attrs = exp.get("attributes", {})
            return (attrs.get("created-at") or attrs.get("created_at") or "", exp.get("id", ""))

        matching.sort(key=sort_key, reverse=True)
        latest_id = matching[0]["id"]
        logger.info(f"Found existing matching export job. ID: {latest_id}")
        return str(latest_id)

    def check_export(self, export_id: str) -> str | None:
        """Check the export once: return its download URL, or None while it is still processing.

        Any non-200 response, 429 included, raises requests.HTTPError so the caller decides how to back off.
        """
        response = requests.get(f"{self.config.api_endpoint}/exports/{export_id}", headers=self.headers, timeout=30)
        if response.status_code != 200:
            if response.status_code != 429:
                logger.error(f"Failed to fetch export status. Status: {response.status_code}, Body: {response.text}")
            raise requests.HTTPError(f"Export status check returned {response.status_code}", response=response)

        attributes = response.json().get("data", {}).get("attributes", {})
        download_url = attributes.get("download-url") or attributes.get("download_url")
        status = attributes.get("status") or attributes.get("state")
        status_msg = f"Status: '{status}'" if status else "Status: processing"
        if download_url:
            logger.info(f"Export complete! Download URL obtained. ({status_msg})")
            return download_url
        logger.info(f"Export {export_id} still in progress. ({status_msg})")
        return None

    def poll_export_status(self, export_id: str, poll_interval_sec: int = 60, timeout_min: int = 120) -> str:
        """Poll until the export has a download URL and return it."""
        start_time = time.time()
        timeout_sec = timeout_min * 60

        logger.info(f"Polling export status for ID {export_id} every {poll_interval_sec} seconds (Timeout: {timeout_min} min)...")
        while (time.time() - start_time) < timeout_sec:
            try:
                download_url = self.check_export(export_id)
            except requests.HTTPError as e:
                if e.response is None or e.response.status_code != 429:
                    raise
                logger.warning("Rate limit hit during polling. Sleeping for a longer interval...")
                time.sleep(poll_interval_sec * 2)
                continue
            if download_url:
                return download_url

            elapsed_min = int((time.time() - start_time) / 60)
            logger.info(f"Elapsed: {elapsed_min} min.")
            time.sleep(poll_interval_sec)

        raise TimeoutError(f"Export job did not complete within the {timeout_min} minute timeout period.")

    def download_export(self, download_url: str, output_path: Path) -> int | None:
        """Stream the export archive to output_path. Returns the server's Content-Length, if it sent one."""
        logger.info(f"Downloading archive from {urlsplit(download_url).netloc}...")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with redacted_download_errors(download_url):
            # No IT Glue headers: the URL is presigned and extra headers break its signature.
            response = requests.get(download_url, stream=True, timeout=120)
            response.raise_for_status()

            total = int(response.headers.get("content-length", 0))
            downloaded = 0
            last_log_time = time.time()
            with open(output_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if not chunk:
                        continue
                    f.write(chunk)
                    downloaded += len(chunk)
                    now = time.time()
                    if now - last_log_time > 10:
                        percent = (downloaded / total * 100) if total else 0
                        logger.info(f"Download progress: {downloaded / (1024*1024):.1f}MB / {total / (1024*1024):.1f}MB ({percent:.1f}%)")
                        last_log_time = now

        logger.info(f"Download completed successfully. Saved to: {output_path}")
        return total or None
