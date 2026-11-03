from pathlib import Path
from typing import Protocol, runtime_checkable


class Storage(Protocol):
    """Where a downloaded and verified Raw Archive ends up."""

    def store(self, archive: Path) -> str:
        """Store the archive and return an identifier for where it went."""
        ...


@runtime_checkable
class StreamingStorage(Protocol):
    """A backend that takes the export straight from its download URL; nothing touches local disk."""

    def store_stream(self, download_url: str, filename: str) -> str:
        """Stream the export to the backend under filename and return an identifier for where it went."""
        ...
