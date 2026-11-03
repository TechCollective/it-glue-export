import logging
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


class VerifyError(Exception):
    """The downloaded archive is not a complete, readable ZIP."""


def verify_archive(path: Path, password: str | None = None, expected_size: int | None = None) -> None:
    """Read-only check of a Raw Archive: size, ZIP structure and every member's CRC. Raises VerifyError."""
    if not path.is_file():
        raise VerifyError(f"{path} does not exist")
    size = path.stat().st_size
    if expected_size is not None and size != expected_size:
        raise VerifyError(f"size mismatch: got {size} bytes, expected {expected_size}")
    if size == 0 or not zipfile.is_zipfile(path):
        raise VerifyError(f"{path.name} is not a ZIP archive")

    logger.info(f"Verifying {path.name} ({size / 1048576:.1f} MB)...")
    with zipfile.ZipFile(path) as zf:
        if not zf.namelist():
            raise VerifyError(f"{path.name} has no members")
        if password:
            zf.setpassword(password.encode("utf-8"))
        # shortcut: pure-Python ZipCrypto decryption makes testzip take ~6 min on an 800 MB
        # encrypted archive; switch to `7z t -p` if that cost matters on the cron host.
        try:
            bad = zf.testzip()
        except (RuntimeError, zipfile.BadZipFile, NotImplementedError) as e:
            raise VerifyError(f"could not read {path.name}: {e}") from e
    if bad:
        raise VerifyError(f"CRC check failed for member {bad!r}")
    logger.info(f"Archive verified: {len(zf.namelist())} members, all CRCs match.")
