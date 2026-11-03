from pathlib import Path
from typing import Protocol


class Storage(Protocol):
    """Where a downloaded Raw Archive ends up."""

    def store(self, archive: Path) -> str:
        """Store the archive and return an identifier for where it went."""
        ...
