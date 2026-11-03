from unittest.mock import patch

import pytest

from itglue_export import cli


def test_backup_flags_parse():
    args = cli.build_parser().parse_args(["backup", "--env", "x.env", "--local", "--simple"])
    assert (args.command, args.env, args.local, args.simple) == ("backup", "x.env", True, True)


def test_backup_subcommand_required():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_simple_is_a_logged_noop(config, caplog):
    with patch("itglue_export.cli.Configuration", return_value=config), \
         patch("itglue_export.cli.BackupOrchestrator") as orch:
        cli.main(["backup", "--local", "--simple"])
    assert orch.return_value.run.called
    assert "deprecated" in caplog.text


def test_failure_exits_nonzero(config):
    with patch("itglue_export.cli.Configuration", return_value=config), \
         patch("itglue_export.cli.BackupOrchestrator") as orch:
        orch.return_value.run.side_effect = RuntimeError("boom")
        with pytest.raises(SystemExit) as exc:
            cli.main(["backup", "--local"])
    assert exc.value.code == 1
