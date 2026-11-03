from pathlib import Path

from itglue_export import cli
from itglue_export.index import build_payload, render

FIXTURE = Path(__file__).parent / "fixtures" / "export"


def test_payload_marks_secret_columns_and_skips_logs():
    p = build_payload(FIXTURE, include_logs=False)
    pw = p["tables"]["passwords"]
    assert pw["secret"] == [False, False, False, False, True, False]
    assert "logs" not in p["tables"]
    assert [o["name"] for o in p["orgs"]] == ["Acme Corp", "Globex"]
    assert {o["name"]: o["total"] for o in p["orgs"]} == {"Acme Corp": 1, "Globex": 1}


def test_include_logs_folds_logs_back_in():
    assert "logs" in build_payload(FIXTURE, include_logs=True)["tables"]


def test_render_keeps_cell_values_out_of_markup():
    html = render(build_payload(FIXTURE, include_logs=False))
    assert "<script>alert(1)" not in html
    assert "alert(1) & co" in html  # tags stripped by the cell cleaner; the page's esc() handles the rest
    assert "function esc(" in html


def test_index_command_writes_default_and_custom_output(tmp_path, capsys):
    import shutil

    export = tmp_path / "export"
    shutil.copytree(FIXTURE, export)
    cli.main(["index", str(export)])
    assert (export / "index.html").stat().st_size > 0

    out = tmp_path / "custom.html"
    cli.main(["index", str(export), "-o", str(out), "--include-logs"])
    assert "logs" in out.read_text()
    assert "Wrote" in capsys.readouterr().out


def test_index_command_rejects_non_export_dir(tmp_path):
    import pytest

    with pytest.raises(SystemExit) as exc:
        cli.main(["index", str(tmp_path)])
    assert exc.value.code == 1
