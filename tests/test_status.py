import json
from datetime import datetime, timedelta, timezone

import pytest

from itglue_export.status import Backup, build_status, parse_status, status_json, write_status

T0 = datetime(2026, 11, 1, 2, 0, tzinfo=timezone.utc)
OLD = Backup("itglue-backup-20261025-020000.zip", T0 - timedelta(days=7), 800_000_000, True)
NEW = Backup("itglue-backup-20261101-020000.zip", T0, 810_000_000, True)
EST = timezone(timedelta(hours=-5))


def test_document_matches_sdd_shape_newest_first():
    doc = build_status([OLD, NEW], generated=T0 + timedelta(hours=1))
    assert doc == {
        "generated": "2026-11-01T03:00:00Z",
        "backups": [
            {"filename": NEW.filename, "creation_date": "2026-11-01T02:00:00Z", "filesize": 810_000_000, "validated": True},
            {"filename": OLD.filename, "creation_date": "2026-10-25T02:00:00Z", "filesize": 800_000_000, "validated": True},
        ],
        "version": "2.0.0",
    }


def test_timestamps_are_normalised_to_utc():
    local = Backup("x.zip", datetime(2026, 11, 1, 21, 0, tzinfo=EST), 1, False)
    assert build_status([local])["backups"][0]["creation_date"] == "2026-11-02T02:00:00Z"


def test_empty_inventory():
    doc = build_status([], generated=T0)
    assert doc["backups"] == [] and doc["version"] == "2.0.0"


def test_write_and_parse_round_trip(tmp_path):
    path = tmp_path / "status.json"
    write_status(path, [OLD, NEW], generated=T0)
    assert json.loads(path.read_text())["generated"] == "2026-11-01T02:00:00Z"
    assert parse_status(path.read_text()) == [NEW, OLD]


def test_status_json_ends_with_newline():
    assert status_json([], generated=T0).endswith("}\n")


def test_rejects_naive_datetime_and_negative_size():
    with pytest.raises(ValueError):
        Backup("x.zip", datetime(2026, 11, 1), 1, True)
    with pytest.raises(ValueError):
        Backup("x.zip", T0, -1, True)
