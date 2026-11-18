"""AC-4: zip archives are inspected, never modified."""
from __future__ import annotations

import hashlib
import zipfile

from conftest import make_demo, nfc, run_cli, scan_mod, scan_tree

import pytest


def sha(p) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def test_nfd_entries_and_legacy_entry(tmp_path):
    z = make_demo.build_names_zip(tmp_path / "a.zip")
    before = sha(z)
    report, _ = scan_tree(tmp_path, [str(z)], no_git=True, no_refs=True)
    assert report["summary"] == {"zip_legacy_name": 1, "zip_nfd_name": 2}
    legacy = next(f for f in report["findings"] if f["kind"] == "zip_legacy_name")
    assert legacy["raw_hex"] == "한글.txt".encode("cp949").hex()
    assert legacy["guess"] == {"encoding": "cp949", "text": "한글.txt"}
    assert sha(z) == before                              # the archive is never modified


def test_collision_in_zip_needs_a_human(tmp_path):
    z = make_demo.build_collision_zip(tmp_path / "c.zip")
    report, _ = scan_tree(tmp_path, [str(z)], no_git=True)
    col = next(f for f in report["findings"] if f["kind"] == "zip_collision")
    assert [m["path_hex"] for m in col["members"]] == ["63616665cc812e747874", "636166c3a92e747874"]
    assert report["exit_code"] == 3


def test_ascii_only_zip_has_no_findings(tmp_path):
    z = tmp_path / "plain.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("a.txt", "x")
        zf.writestr("dir/b.txt", "y")
    report, _ = scan_tree(tmp_path, [str(z)], no_git=True)
    assert report["findings"] == [] and report["exit_code"] == 0


def test_nfc_entries_are_clean(tmp_path):
    z = tmp_path / "nfc.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr(nfc("보고서/요약.txt"), "x")
    report, _ = scan_tree(tmp_path, [str(z)], no_git=True)
    assert report["findings"] == []


def test_truncated_zip_is_an_input_error(tmp_path, capsys):
    z = make_demo.build_truncated_zip(tmp_path / "backup.zip")
    code, _, err = run_cli(scan_mod, [str(tmp_path), "--zip", str(z), "--no-git", "--lang", "en"], capsys)
    assert code == 2 and "not a valid zip file" in err and "backup.zip" in err


def test_truncated_zip_message_in_korean(tmp_path, capsys):
    z = make_demo.build_truncated_zip(tmp_path / "backup.zip")
    code, _, err = run_cli(scan_mod, [str(tmp_path), "--zip", str(z), "--no-git", "--lang", "ko"], capsys)
    assert code == 2 and "올바른 zip 파일이 아닙니다" in err


def test_missing_zip_is_an_input_error(tmp_path, capsys):
    code, _, err = run_cli(scan_mod, [str(tmp_path), "--zip", str(tmp_path / "none.zip"), "--no-git", "--lang", "en"], capsys)
    assert code == 2 and "path not found" in err


def test_not_a_zip_at_all(tmp_path, capsys):
    f = tmp_path / "x.zip"
    f.write_text("hello")
    code, _, err = run_cli(scan_mod, [str(tmp_path), "--zip", str(f), "--no-git", "--lang", "en"], capsys)
    assert code == 2 and "not a valid zip file" in err


def test_table_shows_zip_and_guess(tmp_path, capsys):
    z = make_demo.build_names_zip(tmp_path / "a.zip")
    code, out, _ = run_cli(scan_mod, [str(tmp_path), "--zip", str(z), "--no-git", "--lang", "en"], capsys)
    assert code == 1 and "zip_legacy_name" in out and "guess (cp949)" in out


@pytest.mark.parametrize("name", ["names", "collision"])
def test_helper_zips_are_deterministic(tmp_path, name):
    build = {"names": make_demo.build_names_zip, "collision": make_demo.build_collision_zip}[name]
    assert sha(build(tmp_path / "1.zip")) == sha(build(tmp_path / "2.zip"))
