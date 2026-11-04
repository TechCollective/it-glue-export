"""The status.json inventory (itglue-vault SDD §3.2): what is stored, not how a run went."""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

STATUS_VERSION = "2.0.0"


@dataclass(frozen=True)
class Backup:
    filename: str
    creation_date: datetime  # timezone-aware
    filesize: int
    validated: bool  # the archive passed verification before it was committed

    def __post_init__(self):
        if self.creation_date.tzinfo is None:
            raise ValueError(f"creation_date for {self.filename} must be timezone-aware")
        if self.filesize < 0:
            raise ValueError(f"filesize for {self.filename} must not be negative")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_status(backups: Iterable[Backup], generated: datetime | None = None) -> dict:
    """The status.json document, backups newest first."""
    ordered = sorted(backups, key=lambda b: b.creation_date, reverse=True)
    return {
        "generated": _iso(generated or datetime.now(timezone.utc)),
        "backups": [
            {
                "filename": b.filename,
                "creation_date": _iso(b.creation_date),
                "filesize": b.filesize,
                "validated": b.validated,
            }
            for b in ordered
        ],
        "version": STATUS_VERSION,
    }


def status_json(backups: Iterable[Backup], generated: datetime | None = None) -> str:
    return json.dumps(build_status(backups, generated), indent=2) + "\n"


def write_status(path: Path, backups: Iterable[Backup], generated: datetime | None = None) -> None:
    path.write_text(status_json(backups, generated), encoding="utf-8")


def parse_status(text: str) -> list[Backup]:
    """Read a status.json document back into Backup entries (newest first, as stored)."""
    data = json.loads(text)
    return [
        Backup(
            filename=b["filename"],
            creation_date=datetime.strptime(b["creation_date"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc),
            filesize=int(b["filesize"]),
            validated=bool(b["validated"]),
        )
        for b in data["backups"]
    ]
