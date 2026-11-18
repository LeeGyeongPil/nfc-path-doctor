"""AC-1: show.py classifies forms and reports exact code points and bytes."""
from __future__ import annotations

import json
import os
import sys

import pytest

from conftest import nfc, nfd, run_cli, show_mod


def show_json(capsys, *args: str) -> dict:
    code, out, err = run_cli(show_mod, [*args, "--json", "--lang", "en"], capsys)
    assert code == 0, err
    return json.loads(out)


def test_hangul_nfc_and_nfd_exact_values(capsys):
    doc = show_json(capsys, nfc("한글문서"), nfd("한글문서"))
    a, b = doc["inputs"]
    assert (a["form"], a["len_cp"], len(a["utf8_hex"]) // 2) == ("nfc", 4, 12)
    assert a["utf8_hex"] == "ed959ceab880ebacb8ec849c"
    assert (b["form"], b["len_cp"], len(b["utf8_hex"]) // 2) == ("nfd", 11, 33)
    assert b["codepoints"][:3] == ["U+1112", "U+1161", "U+11AB"]
    assert b["utf8_hex"].startswith("e18492e185a1e186ab")
    assert b["nfc_hex"] == a["utf8_hex"]
    assert doc["same_bytes"] is False
    assert doc["same_nfc"] is True


def test_accent_and_japanese_voiced_mark(capsys):
    doc = show_json(capsys, nfd("café"), nfd("が"))
    cafe, ga = doc["inputs"]
    assert "U+0301" in cafe["codepoints"] and cafe["form"] == "nfd"
    assert ga["codepoints"] == ["U+304B", "U+3099"]


def test_ascii_neutral_and_mixed(capsys):
    doc = show_json(capsys, "readme.txt", "中文", nfc("한") + nfd("글"))
    assert [i["form"] for i in doc["inputs"]] == ["ascii", "neutral", "mixed"]


def test_undecodable_bytes_are_reported_not_crashed(capsys):
    doc = show_json(capsys, "bad\udcff.txt")
    rec = doc["inputs"][0]
    assert rec["form"] == "undecodable"
    assert rec["utf8_hex"] == "626164ff2e747874"  # the raw byte 0xff survives


def test_identical_inputs_compare_equal(capsys):
    doc = show_json(capsys, nfc("한글"), nfc("한글"))
    assert doc["same_bytes"] is True and doc["same_nfc"] is True


def test_file_input_one_per_line(tmp_path, capsys):
    f = tmp_path / "names.txt"
    f.write_bytes((nfc("café") + "\n" + nfd("café") + "\n").encode("utf-8"))
    doc = show_json(capsys, "--file", str(f))
    assert [i["form"] for i in doc["inputs"]] == ["nfc", "nfd"]
    assert doc["same_bytes"] is False and doc["same_nfc"] is True


def test_path_shows_the_bytes_stored_on_disk(tmp_path, capsys, monkeypatch):
    (tmp_path / nfd("자료")).mkdir()
    (tmp_path / nfd("자료") / nfd("한글.txt")).write_text("x")
    monkeypatch.chdir(tmp_path)
    doc = show_json(capsys, "--path", nfc("자료") + "/" + nfc("한글.txt"))
    rec = doc["inputs"][0]
    # the typed NFC path finds the file, and the report shows what the disk really stores
    assert rec["typed_hex"] == (nfc("자료") + "/" + nfc("한글.txt")).encode().hex()
    assert rec["utf8_hex"] == (nfd("자료") + "/" + nfd("한글.txt")).encode().hex()
    assert rec["nfc_hex"] == rec["typed_hex"]


def test_path_missing_is_an_input_error(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, _, err = run_cli(show_mod, ["--path", "nope/x", "--lang", "en"], capsys)
    assert code == 2 and "path not found" in err


def test_no_input_is_an_input_error(capsys):
    code, _, err = run_cli(show_mod, ["--lang", "en"], capsys)
    assert code == 2 and "nothing to show" in err


def test_missing_file_input(capsys, tmp_path):
    code, _, err = run_cli(show_mod, ["--file", str(tmp_path / "none.txt"), "--lang", "en"], capsys)
    assert code == 2 and "file not found" in err


def test_text_output_and_out_file(tmp_path, capsys):
    out = tmp_path / "names.json"
    code, text, _ = run_cli(show_mod, [nfc("café"), nfd("café"), "--out", str(out), "--lang", "en"], capsys)
    assert code == 0
    assert "same bytes: no" in text and "same after NFC: yes" in text and "U+0301" in text
    assert json.loads(out.read_text())["same_nfc"] is True


def test_out_file_error(capsys, tmp_path):
    code, _, err = run_cli(show_mod, ["abc", "--out", str(tmp_path / "no-dir" / "x.json"), "--lang", "en"], capsys)
    assert code == 2 and "error:" in err


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS rejects names that are not valid UTF-8")
def test_path_with_invalid_utf8_name(tmp_path, capsys, monkeypatch):
    bad = os.path.join(os.fsencode(tmp_path), b"bad\xff.txt")
    open(bad, "wb").close()
    monkeypatch.chdir(tmp_path)
    doc = show_json(capsys, "--path", os.fsdecode(b"bad\xff.txt"))
    assert doc["inputs"][0]["form"] == "undecodable"
