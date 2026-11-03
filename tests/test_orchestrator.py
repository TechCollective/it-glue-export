from unittest.mock import MagicMock, patch

import pytest

from itglue_export.orchestrator import BackupOrchestrator
from itglue_export.verify import VerifyError


class FakeStorage:
    def __init__(self):
        self.stored = []

    def store(self, archive):
        self.stored.append(archive)
        return str(archive)


def _orchestrator(config):
    storage = FakeStorage()
    orch = BackupOrchestrator(config, storage=storage)
    orch.client = MagicMock()
    orch.client.trigger_export.return_value = "42"
    orch.client.poll_export_status.return_value = "https://dl.example.test/x.zip"
    orch.client.download_export.side_effect = lambda url, path: (path.write_bytes(b"zip"), 3)[1]
    return orch, storage


def test_verified_archive_is_stored(config):
    orch, storage = _orchestrator(config)
    with patch("itglue_export.orchestrator.verify_archive") as verify:
        orch.run()
    assert len(storage.stored) == 1
    assert storage.stored[0].name.startswith("itglue-backup-") and storage.stored[0].suffix == ".zip"
    verify.assert_called_once_with(storage.stored[0], password=config.zip_password, expected_size=3)


def test_verify_failure_stores_nothing(config):
    orch, storage = _orchestrator(config)
    with patch("itglue_export.orchestrator.verify_archive", side_effect=VerifyError("bad CRC")):
        with pytest.raises(VerifyError):
            orch.run()
    assert storage.stored == []
