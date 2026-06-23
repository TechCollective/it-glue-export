#!/usr/bin/env python3
"""
IT Glue Backup Tool

This script automates the backup of IT Glue data using the IT Glue Exports API
and uploads the resulting ZIP archive to Google Drive. It is designed to run
weekly as a cron job on Linux systems.

Author: Antigravity AI
Date: June 2026
"""

import os
import sys
import time
import logging
import argparse
import requests
import re
import zipfile
import shutil
import csv
import subprocess
from pathlib import Path
from typing import Optional, List, Dict, Any
from dotenv import load_dotenv

# Google Client Libraries
try:
    from google.oauth2 import service_account
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
except ImportError:
    print(
        "Error: Required Google APIs client libraries are not installed. "
        "Please run 'pip install -r requirements.txt' inside a virtual environment.",
        file=sys.stderr
    )
    sys.exit(1)

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("itglue_backup.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("itglue_backup")


class Configuration:
    """
    Configuration Manager

    Responsibility: Load, validate, and provide configuration parameters from
    environment variables or command-line arguments.
    """

    def __init__(self, env_path: Optional[str] = None, simple: bool = False, local: bool = False):
        """Initialize configuration from environment variables."""
        if env_path and os.path.exists(env_path):
            load_dotenv(env_path)
        else:
            load_dotenv()  # Fallback to standard .env load

        self.api_key = os.getenv("ITGLUE_API_KEY")
        self.api_endpoint = os.getenv("ITGLUE_API_ENDPOINT", "https://api.itglue.com").rstrip("/")
        self.zip_password = os.getenv("ITGLUE_ZIP_PASSWORD")
        
        # Parse optional Org ID
        org_id_str = os.getenv("ITGLUE_ORG_ID")
        self.org_id = int(org_id_str) if org_id_str and org_id_str.strip() else None

        # Parse include-logs option
        include_logs_str = os.getenv("ITGLUE_INCLUDE_LOGS", "false").lower()
        self.include_logs = include_logs_str in ("true", "1", "yes")

        # Google Drive settings
        self.gdrive_credentials_file = os.getenv("GDRIVE_CREDENTIALS_FILE", "service-account.json")
        self.gdrive_folder_id = os.getenv("GDRIVE_FOLDER_ID")

        # Local temp storage
        self.local_temp_dir = os.getenv("LOCAL_TEMP_DIR", "./temp")

        # CLI flags
        self.simple = simple
        self.local = local

    def validate(self) -> None:
        """Validate critical configuration fields."""
        missing = []
        if not self.api_key:
            missing.append("ITGLUE_API_KEY")

        # Google Drive validation is only required if we are not running locally
        if not self.local:
            if not self.gdrive_folder_id:
                missing.append("GDRIVE_FOLDER_ID")
            if not os.path.exists(self.gdrive_credentials_file):
                missing.append(f"GDRIVE_CREDENTIALS_FILE ({self.gdrive_credentials_file} not found)")

        if missing:
            raise ValueError(f"Missing required configuration: {', '.join(missing)}")


class ITGlueClient:
    """
    IT Glue API Client

    Responsibility: Interact with the IT Glue Exports API, including triggering,
    polling, and downloading backup ZIP archives.
    """

    def __init__(self, config: Configuration):
        self.config = config
        self.headers = {
            "x-api-key": self.config.api_key,
            "Content-Type": "application/vnd.api+json"
        }

    def trigger_export(self) -> str:
        """
        Triggers a new export job on the IT Glue platform.

        Returns:
            str: The ID of the created export job.
        """
        url = f"{self.config.api_endpoint}/exports"
        
        # Construct JSON API standard payload
        attributes: Dict[str, Any] = {
            "include-logs": self.config.include_logs
        }
        
        if self.config.org_id:
            attributes["organization-id"] = self.config.org_id
            
        if self.config.zip_password:
            attributes["zip-password"] = self.config.zip_password

        payload = {
            "data": {
                "type": "exports",
                "attributes": attributes
            }
        }

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
            logger.info(f"Export job triggered successfully. Export ID: {export_id}")
            return str(export_id)
        except KeyError as e:
            logger.error(f"Failed to parse export ID from response: {data}")
            raise KeyError("Invalid response structure from IT Glue API") from e

    def get_latest_matching_export(self) -> str:
        """
        Retrieves the latest export job matching our configuration.
        
        Returns:
            str: The ID of the latest matching export job.
        """
        url = f"{self.config.api_endpoint}/exports"
        response = requests.get(url, headers=self.headers, timeout=30)
        response.raise_for_status()
        
        data = response.json()
        exports = data.get("data", [])
        
        matching_exports = []
        for export in exports:
            attrs = export.get("attributes", {})
            org_id = attrs.get("organization-id") or attrs.get("organization_id")
            
            # Match the organization ID (both None for full export, or both specific integer IDs)
            if org_id == self.config.org_id:
                matching_exports.append(export)
                
        if not matching_exports:
            raise ValueError("No matching exports found despite 422 error.")
            
        def get_sort_key(exp):
            attrs = exp.get("attributes", {})
            created_at = attrs.get("created-at") or attrs.get("created_at") or ""
            return (created_at, exp.get("id", ""))
            
        matching_exports.sort(key=get_sort_key, reverse=True)
        latest_id = matching_exports[0]["id"]
        logger.info(f"Found existing matching export job. ID: {latest_id}")
        return str(latest_id)

    def poll_export_status(self, export_id: str, poll_interval_sec: int = 60, timeout_min: int = 120) -> str:
        """
        Polls the export job status until the download URL is available.

        Args:
            export_id (str): The ID of the export job.
            poll_interval_sec (int): Frequency of status checks in seconds.
            timeout_min (int): Maximum time to wait in minutes.

        Returns:
            str: The download URL for the export.
        """
        url = f"{self.config.api_endpoint}/exports/{export_id}"
        start_time = time.time()
        timeout_sec = timeout_min * 60

        logger.info(f"Polling export status for ID {export_id} every {poll_interval_sec} seconds (Timeout: {timeout_min} min)...")
        
        while (time.time() - start_time) < timeout_sec:
            response = requests.get(url, headers=self.headers, timeout=30)
            if response.status_code == 429:
                logger.warning("Rate limit hit during polling. Sleeping for a longer interval...")
                time.sleep(poll_interval_sec * 2)
                continue
            elif response.status_code != 200:
                logger.error(f"Failed to fetch export status. Status: {response.status_code}, Body: {response.text}")
                response.raise_for_status()

            data = response.json()
            attributes = data.get("data", {}).get("attributes", {})
            
            # Check both kebab-case and snake_case for maximum resilience
            download_url = attributes.get("download-url") or attributes.get("download_url")
            
            # Check if there is a status field we can log
            status = attributes.get("status") or attributes.get("state")
            status_msg = f"Status: '{status}'" if status else "Status: processing"
            
            if download_url:
                logger.info(f"Export complete! Download URL obtained. ({status_msg})")
                return download_url
            
            elapsed_min = int((time.time() - start_time) / 60)
            logger.info(f"Export still in progress... Elapsed: {elapsed_min} min. ({status_msg})")
            time.sleep(poll_interval_sec)

        raise TimeoutError(f"Export job did not complete within the {timeout_min} minute timeout period.")

    def download_export(self, download_url: str, output_path: Path) -> None:
        """
        Downloads the export ZIP archive from the given URL.
        Streams the download to keep memory usage low for large files.

        Args:
            download_url (str): The URL to download from.
            output_path (Path): The target filepath to save the zip.
        """
        logger.info(f"Downloading archive from {download_url}...")
        
        # Ensure parent directory exists
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        # The download endpoint doesn't require IT Glue headers usually since it's an AWS S3 pre-signed URL,
        # but we download without the custom headers to prevent AWS signature mismatch errors.
        response = requests.get(download_url, stream=True, timeout=120)
        response.raise_for_status()
        
        total_size_bytes = int(response.headers.get('content-length', 0))
        chunk_size = 1024 * 1024  # 1MB chunks
        downloaded_bytes = 0
        
        last_log_time = time.time()
        
        with open(output_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if chunk:
                    f.write(chunk)
                    downloaded_bytes += len(chunk)
                    
                    # Log progress every 10 seconds or when finished
                    current_time = time.time()
                    if current_time - last_log_time > 10:
                        percent = (downloaded_bytes / total_size_bytes * 100) if total_size_bytes > 0 else 0
                        logger.info(f"Download progress: {downloaded_bytes / (1024*1024):.1f}MB / {total_size_bytes / (1024*1024):.1f}MB ({percent:.1f}%)")
                        last_log_time = current_time

        logger.info(f"Download completed successfully. Saved to: {output_path}")


class GoogleDriveUploader:
    """
    Google Drive API Service Wrapper

    Responsibility: Authenticate via service account and upload files to Google Drive.
    """

    def __init__(self, config: Configuration):
        self.config = config
        self.creds = service_account.Credentials.from_service_account_file(
            self.config.gdrive_credentials_file,
            scopes=["https://www.googleapis.com/auth/drive"]
        )
        self.service = build("drive", "v3", credentials=self.creds)

    def upload_file(self, filepath: Path) -> str:
        """
        Uploads a file to Google Drive under the configured folder ID.

        Args:
            filepath (Path): Local file path to upload.

        Returns:
            str: Google Drive File ID of the uploaded archive.
        """
        filename = filepath.name
        logger.info(f"Starting upload of {filename} to Google Drive folder '{self.config.gdrive_folder_id}'...")

        file_metadata = {
            "name": filename,
            "parents": [self.config.gdrive_folder_id]
        }
        
        # Use resumable upload for larger backup sizes (837MB+)
        media = MediaFileUpload(
            str(filepath),
            mimetype="application/zip",
            resumable=True
        )

        request = self.service.files().create(
            body=file_metadata,
            media_body=media,
            fields="id",
            supportsAllDrives=True
        )

        response = None
        last_progress_log = 0.0
        
        while response is None:
            status, response = request.next_chunk()
            if status:
                percent = int(status.progress() * 100)
                if percent - last_progress_log >= 10 or percent == 100:
                    logger.info(f"Upload progress: {percent}%")
                    last_progress_log = percent

        drive_file_id = response.get("id")
        logger.info(f"Upload complete. Google Drive File ID: {drive_file_id}")
        return drive_file_id


class HtmlReportGenerator:
    """
    HTML Report Generator

    Responsibility: Extracts the downloaded IT Glue ZIP archive, parses CSV files,
    generates searchable/interactive HTML files for organization resources (passwords, assets, etc.),
    and repackages them back into a new ZIP archive.
    """

    def __init__(self, temp_dir: Path):
        self.temp_dir = temp_dir
        self.extracted_dir = temp_dir / "extracted"

    def generate(self, zip_path: Path, zip_password: Optional[str] = None) -> Path:
        """
        Processes the ZIP file, injecting HTML reports.
        
        Args:
            zip_path (Path): Path to the source ZIP file.
            zip_password (str): Optional password to decrypt and re-encrypt the ZIP.

        Returns:
            Path: Path to the newly packaged ZIP file.
        """
        logger.info("Extracting download zip file for HTML generation...")
        if self.extracted_dir.exists():
            shutil.rmtree(self.extracted_dir)
        self.extracted_dir.mkdir(parents=True, exist_ok=True)

        pwd = zip_password.encode('utf-8') if zip_password else None
        with zipfile.ZipFile(zip_path, 'r') as ref:
            ref.extractall(self.extracted_dir, pwd=pwd)

        # 1. Parse organizations to map ID to name
        org_map = self._load_organizations()

        # 2. Process all CSV files in the extracted root directory
        csv_files = list(self.extracted_dir.glob("*.csv"))
        if not csv_files:
            logger.warning("No CSV files found in the extracted zip. Skipping HTML generation.")
            shutil.rmtree(self.extracted_dir)
            return zip_path

        for csv_file in csv_files:
            if csv_file.name in ("organizations.csv", "logs.csv"):
                continue
            self._process_csv_to_html(csv_file, org_map)

        # 3. Create a new ZIP file
        new_zip_path = zip_path.parent / f"processed_{zip_path.name}"
        
        if zip_password:
            # We must use the system `zip` utility to create an encrypted ZIP,
            # since Python's standard `zipfile` module does not support writing encrypted zips.
            logger.info(f"Encrypting and re-packaging processed files into {new_zip_path.name}...")
            if new_zip_path.exists():
                new_zip_path.unlink()
            
            # Run command: zip -q -r -P password /path/to/new_zip.zip .
            cmd = ["zip", "-q", "-r", "-P", zip_password, str(new_zip_path.resolve()), "."]
            subprocess.run(cmd, cwd=self.extracted_dir, check=True)
        else:
            logger.info(f"Re-packaging processed files into {new_zip_path.name}...")
            with zipfile.ZipFile(new_zip_path, 'w', zipfile.ZIP_DEFLATED) as new_zip:
                for root, dirs, files in os.walk(self.extracted_dir):
                    for file in files:
                        file_path = Path(root) / file
                        archive_name = file_path.relative_to(self.extracted_dir)
                        new_zip.write(file_path, archive_name)

        # Clean up extracted temp directory
        shutil.rmtree(self.extracted_dir)
        
        # Delete old zip and replace it with the new one
        zip_path.unlink()
        new_zip_path.rename(zip_path)
        logger.info("HTML report injection complete.")
        return zip_path

    def _load_organizations(self) -> Dict[str, str]:
        org_csv = self.extracted_dir / "organizations.csv"
        org_map = {}
        if not org_csv.exists():
            return org_map

        try:
            with open(org_csv, mode="r", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    org_id = row.get("id") or row.get("organization-id") or row.get("organization_id")
                    name = row.get("name") or row.get("organization-name") or row.get("organization_name")
                    if org_id and name:
                        org_map[str(org_id)] = name
        except Exception as e:
            logger.error(f"Failed to load organizations.csv: {e}")
        return org_map

    def _process_csv_to_html(self, csv_file: Path, org_map: Dict[str, str]) -> None:
        try:
            with open(csv_file, mode="r", encoding="utf-8-sig") as f:
                content = f.read()
                # Empty check
                if not content.strip():
                    return
                f.seek(0)
                reader = csv.reader(f)
                headers = next(reader, None)
                if not headers:
                    return
                rows = list(reader)

            org_col_idx = self._find_org_column(headers)
            if org_col_idx is None:
                # If no organization column, just make a single master HTML in the root
                html_content = self._to_searchable_html(csv_file.stem.replace("-", " ").title(), headers, rows)
                html_path = self.extracted_dir / f"{csv_file.stem}.html"
                with open(html_path, "w", encoding="utf-8") as out:
                    out.write(html_content)
                return

            # Group rows by organization
            org_groups = {}
            for row in rows:
                if len(row) <= org_col_idx:
                    continue
                org_val = row[org_col_idx].strip()
                # If it's a digit (Org ID), resolve the name
                org_name = org_map.get(org_val, f"Organization_{org_val}") if org_val.isdigit() else org_val
                if not org_name:
                    org_name = "Global"
                org_groups.setdefault(org_name, []).append(row)

            # Generate individual organization HTML files
            asset_name = csv_file.stem.replace("-", " ").title()
            for org_name, org_rows in org_groups.items():
                sanitized_org = self._sanitize_filename(org_name)
                org_dir = self.extracted_dir / sanitized_org
                org_dir.mkdir(parents=True, exist_ok=True)

                html_content = self._to_searchable_html(f"{org_name} - {asset_name}", headers, org_rows)
                html_path = org_dir / f"{csv_file.stem}.html"
                with open(html_path, "w", encoding="utf-8") as out:
                    out.write(html_content)

            # Also generate a master searchable HTML in the root folder
            master_html_content = self._to_searchable_html(f"All Organizations - {asset_name}", headers, rows)
            master_html_path = self.extracted_dir / f"{csv_file.stem}.html"
            with open(master_html_path, "w", encoding="utf-8") as out:
                out.write(master_html_content)

        except Exception as e:
            logger.error(f"Failed to convert {csv_file.name} to HTML: {e}")

    def _find_org_column(self, headers: List[str]) -> Optional[int]:
        # exact match list
        exact_matches = ["organization-name", "organization_name", "organization-id", "organization_id", "org-name", "org_name", "org-id", "org_id"]
        for match in exact_matches:
            # Case insensitive check
            for idx, h in enumerate(headers):
                if h.lower().strip() == match:
                    return idx
        # partial matches
        for idx, h in enumerate(headers):
            h_lower = h.lower().strip()
            if "organization" in h_lower or "org-" in h_lower or "org_" in h_lower:
                return idx
        return None

    def _sanitize_filename(self, name: str) -> str:
        return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

    def _to_searchable_html(self, title: str, headers: List[str], rows: List[List[str]]) -> str:
        header_cols = "".join(f"<th>{h}</th>" for h in headers)
        row_html = []
        for row in rows:
            cols = "".join(f"<td>{r}</td>" for r in row)
            row_html.append(f"<tr>{cols}</tr>")
        rows_str = "\n".join(row_html)
        
        return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
body {{ background-color:#E5E4E2; font-family: Tahoma, sans-serif; font-size:10pt; padding:20px; }}
h1 {{ font-family: Tahoma; color:#6D7B8D; margin-bottom: 20px; }}
table {{ width:100%; border-collapse:collapse; margin-top:20px; background-color:white; box-shadow: 0 1px 3px rgba(0,0,0,0.2); }}
th, td {{ border:1px solid #ccc; padding:8px; text-align:left; }}
th {{ background-color:#000; color:white; font-weight:bold; }}
tr:nth-child(even) {{ background-color: #f2f2f2; }}
tr:hover {{ background-color: #e9e9e9; }}
#myInput {{ width: 50%; font-size: 14px; padding: 12px 20px; border: 1px solid #ddd; margin-bottom: 12px; border-radius: 4px; }}
</style>
<script>
function filterTable() {{
    const filter = document.getElementById('myInput').value.toUpperCase();
    const trs = document.querySelectorAll('table tr:not(.header)');
    trs.forEach(tr => {{
        const text = [...tr.children].map(td => td.textContent).join(' ').toUpperCase();
        tr.style.display = text.includes(filter) ? '' : 'none';
    }});
}}
</script>
</head>
<body>
<h1>{title}</h1>
<input type="text" id="myInput" onkeyup="filterTable()" placeholder="Search content..." title="Type a query">
<table>
<tr class="header">{header_cols}</tr>
{rows_str}
</table>
</body>
</html>
"""


class BackupOrchestrator:
    """
    Backup Orchestrator

    Responsibility: Orchestrate the end-to-end backup pipeline: trigger export,
    poll status, download zip, upload to Google Drive, and clean up.
    """

    def __init__(self, config: Configuration):
        self.config = config
        self.itglue_client = ITGlueClient(config)
        self.html_generator = HtmlReportGenerator(Path(self.config.local_temp_dir))
        self.uploader = None if self.config.local else GoogleDriveUploader(config)

    def run(self) -> None:
        """Execute the backup pipeline."""
        start_time = time.time()
        logger.info("=========================================")
        logger.info("IT Glue Export Backup Pipeline Started")
        logger.info("=========================================")
        
        temp_filepath: Optional[Path] = None
        
        try:
            # 1. Validate configuration
            self.config.validate()
            
            # 2. Trigger Export job
            export_id = self.itglue_client.trigger_export()
            
            # 3. Poll for download URL
            download_url = self.itglue_client.poll_export_status(export_id)
            
            # 4. Prepare local download path
            timestamp = time.strftime("%Y%m%d-%H%M%S")
            filename = f"itglue-backup-{timestamp}.zip"
            temp_filepath = Path(self.config.local_temp_dir) / filename
            
            # 5. Download ZIP archive
            self.itglue_client.download_export(download_url, temp_filepath)
            
            # Post-process with HTML generation if not simple
            if not self.config.simple:
                temp_filepath = self.html_generator.generate(temp_filepath, zip_password=self.config.zip_password)
            
            # 6. Upload to Google Drive (if not local)
            if self.config.local:
                logger.info(f"Local backup mode enabled. File saved locally at: {temp_filepath.resolve()}")
            else:
                self.uploader.upload_file(temp_filepath)
            
            logger.info("IT Glue backup completed successfully.")
            
        except Exception as e:
            logger.exception("An error occurred during the backup pipeline execution:")
            sys.exit(1)
            
        finally:
            # 7. Clean up local temp ZIP file if not running locally
            if not self.config.local and temp_filepath and temp_filepath.exists():
                try:
                    logger.info(f"Cleaning up temporary file {temp_filepath}...")
                    temp_filepath.unlink()
                    logger.info("Temporary file deleted.")
                except Exception as e:
                    logger.error(f"Failed to delete temporary file {temp_filepath}: {e}")

            duration_min = int((time.time() - start_time) / 60)
            logger.info("=========================================")
            logger.info(f"IT Glue Export Backup Pipeline Finished (Duration: {duration_min} min)")
            logger.info("=========================================")


def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Trigger IT Glue Export, download archive, and upload to Google Drive."
    )
    parser.add_argument(
        "--env",
        type=str,
        help="Path to custom .env configuration file"
    )
    parser.add_argument(
        "--simple",
        action="store_true",
        help="Skip generating HTML reports and keep/upload raw zip only"
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Do not upload to Google Drive, keep the final backup zip locally"
    )
    args = parser.parse_args()

    config = Configuration(env_path=args.env, simple=args.simple, local=args.local)
    orchestrator = BackupOrchestrator(config)
    orchestrator.run()


if __name__ == "__main__":
    main()
