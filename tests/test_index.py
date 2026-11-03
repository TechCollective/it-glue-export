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


def _fixture_zip(tmp_path, password=None):
    import subprocess
    import zipfile

    archive = tmp_path / "itglue-backup-20260101-000000.zip"
    if password:
        subprocess.run(["zip", "-q", "-r", "-P", password, str(archive), "."], cwd=FIXTURE, check=True)
    else:
        with zipfile.ZipFile(archive, "w") as zf:
            for f in FIXTURE.iterdir():
                zf.write(f, f.name)
    return archive


def test_index_from_zip_extracts_to_sibling_and_leaves_zip_untouched(tmp_path):
    archive = _fixture_zip(tmp_path)
    before = archive.read_bytes()
    cli.main(["index", str(archive)])
    extracted = tmp_path / "itglue-backup-20260101-000000"
    assert (extracted / "organizations.csv").exists()
    assert (extracted / "index.html").stat().st_size > 0
    assert archive.read_bytes() == before


def test_index_from_zip_with_password(tmp_path):
    archive = _fixture_zip(tmp_path, password="pw")
    cli.main(["index", str(archive), "--password", "pw"])
    assert (tmp_path / "itglue-backup-20260101-000000" / "index.html").exists()


def test_index_from_zip_wrong_password_fails_clearly(tmp_path, capsys):
    import pytest

    archive = _fixture_zip(tmp_path, password="pw")
    before = archive.read_bytes()
    with pytest.raises(SystemExit) as exc:
        cli.main(["index", str(archive), "--password", "nope"])
    assert exc.value.code == 1
    assert "could not extract" in capsys.readouterr().err
    assert not (tmp_path / "itglue-backup-20260101-000000").exists()
    assert archive.read_bytes() == before
