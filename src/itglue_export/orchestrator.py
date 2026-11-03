import logging
import time
from pathlib import Path

from .client import ITGlueClient
from .config import Configuration
from .storage import LocalStorage, Storage
from .verify import verify_archive

logger = logging.getLogger(__name__)


class BackupOrchestrator:
    """Run the pipeline: trigger, poll, download, verify, store, clean up."""

    def __init__(self, config: Configuration, storage: Storage | None = None):
        self.config = config
        self.client = ITGlueClient(config)
        if storage is None:
            if config.local:
                storage = LocalStorage()
            else:
                from .storage.gdrive import GoogleDriveStorage

                storage = GoogleDriveStorage(config)
        self.storage = storage

    def run(self) -> None:
        start_time = time.time()
        logger.info("=========================================")
        logger.info("IT Glue Export Backup Pipeline Started")
        logger.info("=========================================")
        archive: Path | None = None
        try:
            self.config.validate()
            export_id = self.client.trigger_export()
            download_url = self.client.poll_export_status(export_id)
            archive = self.config.local_temp_dir / f"itglue-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
            expected_size = self.client.download_export(download_url, archive)
            verify_archive(archive, password=self.config.zip_password, expected_size=expected_size)
            self.storage.store(archive)
            logger.info("IT Glue backup completed successfully.")
        finally:
            if not self.config.local and archive and archive.exists():
                try:
                    logger.info(f"Cleaning up temporary file {archive}...")
                    archive.unlink()
                except OSError as e:
                    logger.error(f"Failed to delete temporary file {archive}: {e}")
            duration_min = int((time.time() - start_time) / 60)
            logger.info("=========================================")
            logger.info(f"IT Glue Export Backup Pipeline Finished (Duration: {duration_min} min)")
            logger.info("=========================================")
