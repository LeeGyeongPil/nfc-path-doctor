"""AC-2 (references part): text references compared with the real bytes on disk."""
from __future__ import annotations

from conftest import kinds, nfc, nfd, scan_tree


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


def test_nfc_link_to_nfd_file_is_flagged_with_line_and_column(tmp_path):
    write(tmp_path / nfd("자료.txt"), "x")
    write(tmp_path / "doc.md", "intro\nsee " + nfc("자료.txt") + " now\n")
    report, plan = scan_tree(tmp_path, no_git=True)
    ref = next(f for f in report["findings"] if f["kind"] == "ref_mismatch")
    assert (ref["file"], ref["line"], ref["col"]) == ("doc.md", 2, 5)
    # the disk name becomes NFC, so an NFC reference needs no rewrite
    assert not [a for a in plan["actions"] if a["op"] == "ref_rewrite"]


def test_nfd_link_to_nfc_file_is_flagged_and_rewritten(tmp_path):
    write(tmp_path / nfc("자료.txt"), "x")
    write(tmp_path / "doc.md", "[d](" + nfd("자료.txt") + ")\n")
    report, plan = scan_tree(tmp_path, no_git=True)
    assert kinds(report) == ["ref_mismatch"]
    rw = [a for a in plan["actions"] if a["op"] == "ref_rewrite"]
    occ = rw[0]["occurrences"][0]
    assert (occ["line"], occ["col"]) == (1, 5)
    assert occ["old_hex"] == nfd("자료.txt").encode().hex() and occ["new_hex"] == nfc("자료.txt").encode().hex()


def test_nfd_link_to_nfd_file_is_consistent_now_but_planned_for_rewrite(tmp_path):
    write(tmp_path / nfd("자료.txt"), "x")
    write(tmp_path / "doc.md", "[d](" + nfd("자료.txt") + ")\n")
    report, plan = scan_tree(tmp_path, no_git=True)
    assert kinds(report) == ["nfd_name"]                 # the link matches the disk today ...
    assert [a["op"] for a in plan["actions"]] == ["ref_rewrite", "fs_rename"]   # ... but would break after the rename


def test_relative_links_resolve_from_the_files_folder(tmp_path):
    write(tmp_path / nfd("이미지") / "a.png", "x")
    write(tmp_path / "docs" / "guide.md", "![a](../" + nfc("이미지") + "/a.png)\n")
    report, _ = scan_tree(tmp_path, no_git=True)
    ref = [f for f in report["findings"] if f["kind"] == "ref_mismatch"]
    assert len(ref) == 1 and ref[0]["location"] == "docs/guide.md:1:6"


def test_root_relative_paths_in_code_are_found(tmp_path):
    write(tmp_path / nfd("데이터") / "a.csv", "x")
    write(tmp_path / "src" / "load.py", 'path = "' + nfc("데이터") + '/a.csv"\n')
    report, _ = scan_tree(tmp_path, no_git=True)
    assert any(f["kind"] == "ref_mismatch" and f["file"] == "src/load.py" for f in report["findings"])


def test_ascii_text_and_unknown_names_are_ignored(tmp_path):
    write(tmp_path / nfd("자료.txt"), "x")
    write(tmp_path / "doc.md", "plain line a.txt\n" + nfc("없는파일.txt") + "\n한국어 문장입니다.\n")
    report, _ = scan_tree(tmp_path, no_git=True)
    assert kinds(report) == ["nfd_name"]


def test_binary_oversized_and_non_utf8_files_are_skipped(tmp_path):
    write(tmp_path / nfd("자료.txt"), "x")
    (tmp_path / "a.md").write_bytes(b"\xff\xfe\x00 not text")
    (tmp_path / "b.md").write_bytes(b"x" * (2 * 1024 * 1024 + 1))
    (tmp_path / "c.md").write_bytes(("ok\0" + nfc("자료.txt")).encode())
    report, _ = scan_tree(tmp_path, no_git=True)
    assert report["stats"]["ref_files_skipped"] == 3
    assert kinds(report) == ["nfd_name"]


def test_ref_ext_and_no_refs(tmp_path):
    write(tmp_path / nfd("자료.txt"), "x")
    write(tmp_path / "doc.cfg", nfc("자료.txt") + "\n")
    assert "ref_mismatch" not in kinds(scan_tree(tmp_path, no_git=True)[0])          # .cfg is not searched by default
    assert "ref_mismatch" in kinds(scan_tree(tmp_path, no_git=True, ref_ext="cfg")[0])
    assert "ref_mismatch" not in kinds(scan_tree(tmp_path, no_git=True, ref_ext="cfg", no_refs=True)[0])


def test_two_references_on_one_line_are_both_planned(tmp_path):
    write(tmp_path / nfc("가.txt"), "x")
    write(tmp_path / nfc("나.txt"), "x")
    write(tmp_path / "doc.md", nfd("가.txt") + " and " + nfd("나.txt") + "\n")
    _, plan = scan_tree(tmp_path, no_git=True)
    occ = plan["actions"][0]["occurrences"]
    assert [o["col"] for o in occ] == [1, 1 + len(nfd("가.txt")) + 5]


def test_own_reports_and_plans_are_not_counted_as_references(tmp_path):
    """Scanning a folder twice gives the same answer even though report.json / plan.json now sit inside it."""
    import scan as scan_mod
    from conftest import make_demo
    site = make_demo.build_site(tmp_path / "site")
    first, plan = scan_tree(tmp_path, no_git=True)
    (tmp_path / "report.json").write_text(scan_mod.n.dumps(first), encoding="utf-8")
    (tmp_path / "plan.json").write_text(scan_mod.n.dumps(plan), encoding="utf-8")
    (tmp_path / "names.json").write_text('{"inputs": [{"text": "site/' + nfc("이미지") + '"}], "same_nfc": true, '
                                         '"version": 1}', encoding="utf-8")
    second, _ = scan_tree(tmp_path, no_git=True)
    assert second["summary"] == first["summary"] == {"nfd_name": 3, "ref_mismatch": 1}
    assert second["stats"]["ref_files_own_reports"] == 3
    assert site.is_dir()


def test_other_json_files_are_still_searched(tmp_path):
    (tmp_path / nfd("가.txt")).write_text("x")
    write(tmp_path / "config.json", '{"version": 1, "file": "./' + nfc("가.txt") + '"}')
    report, _ = scan_tree(tmp_path)
    assert "ref_mismatch" in kinds(report)
    write(tmp_path / "bad.json", '{"version": 1, "root_hex": "./' + nfc("가.txt") + '"')   # not valid JSON: searched
    assert len([f for f in scan_tree(tmp_path)[0]["findings"] if f["kind"] == "ref_mismatch"]) == 2
