import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class LocalStorage:
    """Keep the archive where it was downloaded."""

    def store(self, archive: Path) -> str:
        logger.info(f"Local backup mode enabled. File saved locally at: {archive.resolve()}")
        return str(archive.resolve())
