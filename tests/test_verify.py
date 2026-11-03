import zipfile

import pytest

from itglue_export.verify import VerifyError, verify_archive


@pytest.fixture
def good_zip(tmp_path):
    path = tmp_path / "good.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("organizations.csv", "id,name\n1,Acme\n" * 200)
        zf.writestr("documents/readme.html", "<p>hello</p>" * 500)
    return path


def test_good_archive_passes(good_zip):
    verify_archive(good_zip, expected_size=good_zip.stat().st_size)


def test_corrupted_member_fails(good_zip):
    data = bytearray(good_zip.read_bytes())
    mid = len(data) // 2
    data[mid:mid + 8] = b"\xff" * 8  # inside the compressed data, not the directory
    good_zip.write_bytes(data)
    with pytest.raises(VerifyError):
        verify_archive(good_zip)


def test_truncated_archive_fails(good_zip):
    good_zip.write_bytes(good_zip.read_bytes()[:-40])
    with pytest.raises(VerifyError):
        verify_archive(good_zip)


def test_size_mismatch_fails(good_zip):
    with pytest.raises(VerifyError, match="size mismatch"):
        verify_archive(good_zip, expected_size=good_zip.stat().st_size + 1)


def test_not_a_zip_fails(tmp_path):
    path = tmp_path / "x.zip"
    path.write_bytes(b"not a zip at all")
    with pytest.raises(VerifyError):
        verify_archive(path)


def test_verify_never_modifies_archive(good_zip):
    before = good_zip.read_bytes()
    verify_archive(good_zip)
    assert good_zip.read_bytes() == before
