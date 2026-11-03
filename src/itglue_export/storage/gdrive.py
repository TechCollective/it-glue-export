import logging
from pathlib import Path

from ..config import Configuration

logger = logging.getLogger(__name__)


class GoogleDriveStorage:
    """Upload the archive to a Google Drive folder with a service account (needs the [gdrive] extra)."""

    def __init__(self, config: Configuration):
        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
        except ImportError as e:
            raise ImportError("Google Drive upload needs the [gdrive] extra: uv sync --extra gdrive") from e
        self.config = config
        creds = service_account.Credentials.from_service_account_file(
            config.gdrive_credentials_file, scopes=["https://www.googleapis.com/auth/drive"]
        )
        self.service = build("drive", "v3", credentials=creds)

    def store(self, archive: Path) -> str:
        from googleapiclient.http import MediaFileUpload

        logger.info(f"Starting upload of {archive.name} to Google Drive folder '{self.config.gdrive_folder_id}'...")
        media = MediaFileUpload(str(archive), mimetype="application/zip", resumable=True)
        request = self.service.files().create(
            body={"name": archive.name, "parents": [self.config.gdrive_folder_id]},
            media_body=media,
            fields="id",
            supportsAllDrives=True,
        )
        response = None
        last_logged = 0
        while response is None:
            status, response = request.next_chunk()
            if status:
                percent = int(status.progress() * 100)
                if percent - last_logged >= 10 or percent == 100:
                    logger.info(f"Upload progress: {percent}%")
                    last_logged = percent
        file_id = response.get("id")
        logger.info(f"Upload complete. Google Drive File ID: {file_id}")
        return file_id
