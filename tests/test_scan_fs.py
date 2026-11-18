"""AC-2 (tree part): work tree scan, exit codes and determinism."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from conftest import ROOT, SCRIPTS, kinds, make_demo, nfc, nfd, run_cli, scan_mod, scan_tree


def test_site_tree_finds_three_nfd_names_and_one_link(site):
    report, _ = scan_tree(site, no_git=True)
    assert report["summary"] == {"nfd_name": 3, "ref_mismatch": 1}
    ref = next(f for f in report["findings"] if f["kind"] == "ref_mismatch")
    assert ref["location"] == "README.md:3:9"
    assert ref["path_hex"] == nfc("이미지/로고.png").encode().hex()
    assert ref["disk_hex"] == nfd("이미지/로고.png").encode().hex()
    names = sorted(f["path"] for f in report["findings"] if f["kind"] == "nfd_name")
    assert names == sorted([nfd("이미지"), nfd("이미지") + "/" + nfd("로고.png"), nfd("ガイド.md")])
    assert report["exit_code"] == 1


def test_clean_tree_exits_zero(tmp_path):
    (tmp_path / nfc("한글문서.md")).write_text("hi")
    (tmp_path / "plain.txt").write_text("hi")
    report, plan = scan_tree(tmp_path, no_git=True)
    assert report["findings"] == [] and report["exit_code"] == 0
    assert plan["actions"] == [] and plan["blocked"] == []


def test_mixed_name_is_reported(tmp_path):
    (tmp_path / (nfc("한") + nfd("글") + ".txt")).write_text("x")
    report, plan = scan_tree(tmp_path, no_git=True)
    assert kinds(report) == ["mixed_name"]
    assert plan["actions"][0]["to_hex"] == nfc("한글.txt").encode().hex()


def test_neutral_and_ascii_names_are_not_findings(tmp_path):
    (tmp_path / "中文.txt").write_text("x")
    report, _ = scan_tree(tmp_path, no_git=True)
    assert report["findings"] == []


def test_same_input_gives_identical_json_bytes(site):
    a, _ = scan_tree(site, no_git=True)
    b, _ = scan_tree(site, no_git=True)
    from _npd import dumps
    assert dumps(a) == dumps(b)


def test_missing_root_exits_two_with_message(tmp_path, capsys):
    code, _, err = run_cli(scan_mod, [str(tmp_path / "nope"), "--lang", "en"], capsys)
    assert code == 2 and "path not found" in err


def test_root_must_be_a_directory(tmp_path, capsys):
    f = tmp_path / "file.txt"
    f.write_text("x")
    code, _, err = run_cli(scan_mod, [str(f), "--lang", "en"], capsys)
    assert code == 2 and "not a directory" in err


def test_exclude_skips_entries(site):
    report, _ = scan_tree(site, no_git=True, exclude=[nfc("이미지")])
    assert report["summary"] == {"nfd_name": 1}      # the NFC glob matches the NFD folder; its link no longer resolves


def test_symlinks_are_not_followed(tmp_path):
    target = tmp_path / "real"
    target.mkdir()
    (target / nfd("한글.txt")).write_text("x")
    os.symlink(target, tmp_path / nfd("링크"))
    report, _ = scan_tree(tmp_path, no_git=True)
    paths = [f["path"] for f in report["findings"]]
    assert nfd("링크") in paths                         # the link itself is a name
    assert nfd("링크") + "/" + nfd("한글.txt") not in paths   # but it is not walked


def test_git_dir_is_skipped(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / nfd("한글.txt")).write_text("x")
    report, _ = scan_tree(tmp_path, no_git=True)
    assert report["findings"] == []


def test_casefold_warning_does_not_change_exit(tmp_path, coexist):
    (tmp_path / "Readme.md").write_text("a")
    try:
        (tmp_path / "README.md").write_text("b")
    except OSError:
        pass
    if not (tmp_path / "Readme.md").exists() or len(os.listdir(tmp_path)) < 2:
        pytest.skip("case-insensitive file system cannot hold both names")
    report, _ = scan_tree(tmp_path, no_git=True, casefold=True)
    assert kinds(report) == ["case_collision"] and report["exit_code"] == 0


def test_fs_collision_when_both_forms_coexist(tmp_path, coexist):
    if not coexist:
        pytest.skip("this file system folds NFC and NFD names (APFS); runs in CI on Linux")
    (tmp_path / nfc("café.txt")).write_text("one")
    (tmp_path / nfd("café.txt")).write_text("two")
    report, plan = scan_tree(tmp_path, no_git=True)
    assert "fs_collision" in kinds(report) and report["exit_code"] == 3
    assert plan["actions"] == [] and plan["blocked"][0]["kind"] == "fs_collision"
    assert len(plan["blocked"][0]["members"]) == 2


def test_collision_blocks_descendants_on_linux(tmp_path, coexist):
    if not coexist:
        pytest.skip("this file system folds NFC and NFD names (APFS); runs in CI on Linux")
    for form in (nfc, nfd):
        d = tmp_path / form("폴더")
        d.mkdir()
        (d / nfd("파일.txt")).write_text(form("x"))
    _, plan = scan_tree(tmp_path, no_git=True)
    assert plan["actions"] == []                          # the colliding folders and everything below stay untouched


@pytest.mark.skipif(sys.platform == "darwin", reason="APFS rejects names that are not valid UTF-8")
def test_undecodable_name_needs_a_human(tmp_path):
    open(os.path.join(os.fsencode(tmp_path), b"bad\xff.txt"), "wb").close()
    report, plan = scan_tree(tmp_path, no_git=True)
    assert kinds(report) == ["undecodable_name"] and report["exit_code"] == 3
    assert plan["blocked"][0]["kind"] == "undecodable_name"


def test_unreadable_folder_is_counted(tmp_path):
    d = tmp_path / "locked"
    d.mkdir()
    (d / "x.txt").write_text("x")
    d.chmod(0)
    try:
        if os.access(d, os.R_OK):
            pytest.skip("running with privileges that ignore permissions")
        report, _ = scan_tree(tmp_path, no_git=True)
        assert report["stats"]["skipped_dirs"] == 1
    finally:
        d.chmod(0o755)


def test_cli_table_json_and_files(site, tmp_path):
    out, plan_out = tmp_path / "report.json", tmp_path / "plan.json"
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan.py"), str(site), "--no-git", "--out", str(out),
                        "--plan-out", str(plan_out), "--lang", "en"], capture_output=True, text=True)
    assert r.returncode == 1
    assert "nfd_name 3, ref_mismatch 1" in r.stdout and "README.md:3:9" in r.stdout
    assert json.loads(out.read_text())["summary"] == {"nfd_name": 3, "ref_mismatch": 1}
    assert json.loads(plan_out.read_text())["target"] == "NFC"
    j = subprocess.run([sys.executable, str(SCRIPTS / "scan.py"), str(site), "--no-git", "--json", "--lang", "en"],
                       capture_output=True, text=True)
    assert json.loads(j.stdout)["exit_code"] == 1


def test_table_is_cut_at_max_rows_but_json_is_not(site, capsys):
    code, out, _ = run_cli(scan_mod, [str(site), "--no-git", "--max-rows", "1", "--lang", "en"], capsys)
    assert code == 1 and "more finding(s) not shown" in out
    code, out, _ = run_cli(scan_mod, [str(site), "--no-git", "--json", "--lang", "en"], capsys)
    assert len(json.loads(out)["findings"]) == 4


def test_unwritable_output_is_an_input_error(site, capsys, tmp_path):
    code, _, err = run_cli(scan_mod, [str(site), "--no-git", "--out", str(tmp_path / "no-dir" / "r.json"), "--lang", "en"], capsys)
    assert code == 2 and "error:" in err


def test_demo_builder_cli(tmp_path, capsys):
    dest = tmp_path / "demo"
    assert make_demo.main([str(dest)]) == 0
    assert (dest / "archive.zip").exists() and (dest / ".git").is_dir()
    with pytest.raises(SystemExit):
        make_demo.main([str(dest)])        # refuses to overwrite a used folder
    assert ROOT.exists()


def fake_tree(*rels: str):
    """A Tree built by hand: lets the collision logic run on file systems (APFS) that cannot hold both forms."""
    tree = scan_mod.Tree()
    for rel in rels:
        parent, _, name = rel.rpartition("/")
        kind = "dir" if rel.endswith("#") else "file"
        tree.add(scan_mod.Entry(rel.rstrip("#"), name.rstrip("#"), parent, kind))
    return tree


def test_collision_logic_with_a_hand_built_tree():
    tree = fake_tree(nfc("café.txt"), nfd("café.txt"), "ok.txt")
    findings, blocked = scan_mod.fs_findings(tree, casefold=False)
    assert sorted(f["kind"] for f in findings) == ["fs_collision", "nfd_name"]
    col = next(f for f in findings if f["kind"] == "fs_collision")
    assert [m["path_hex"] for m in col["members"]] == ["63616665cc812e747874", "636166c3a92e747874"]
    plan = scan_mod.build_plan("/x", {}, tree, blocked, [], [], None, [], {})
    assert plan["actions"] == [] and plan["blocked"][0]["kind"] == "fs_collision"


def test_blocked_folder_blocks_everything_below_it():
    tree = fake_tree(nfc("폴더#"), nfd("폴더#"), nfd("폴더") + "/" + nfd("파일.txt"), nfd("다른.txt"))
    _, blocked = scan_mod.fs_findings(tree, casefold=False)
    plan = scan_mod.build_plan("/x", {}, tree, blocked, [], [], None, [], {})
    assert [a["from_hex"] for a in plan["actions"]] == [nfd("다른.txt").encode().hex()]


def test_case_collision_logic_with_a_hand_built_tree():
    tree = fake_tree("Readme.md", "README.md")
    findings, _ = scan_mod.fs_findings(tree, casefold=True)
    assert [f["kind"] for f in findings] == ["case_collision"]
    assert scan_mod.exit_code(findings) == 0


def test_undecodable_entry_is_blocked_with_its_children():
    tree = fake_tree("bad\udcff.txt", "bad\udcff#", "bad\udcff/" + nfd("가.txt"))
    findings, blocked = scan_mod.fs_findings(tree, casefold=False)
    assert {f["kind"] for f in findings} == {"undecodable_name", "nfd_name"}
    plan = scan_mod.build_plan("/x", {}, tree, blocked, [], [], None, [], {})
    assert plan["actions"] == [] and scan_mod.exit_code(findings) == 3


def test_exit_zero_keeps_the_result_code_in_the_report(site, tmp_path, capsys):
    out = tmp_path / "r.json"
    code, text, _ = run_cli(scan_mod, [str(site), "--no-git", "--exit-zero", "--out", str(out), "--lang", "en"], capsys)
    assert code == 0 and json.loads(out.read_text())["exit_code"] == 1
    assert "exit code 1: fixable findings" in text and "--exit-zero: the process exit status is 0" in text
    code, text, _ = run_cli(scan_mod, [str(site), "--no-git", "--exit-zero", "--lang", "ko"], capsys)
    assert code == 0 and "--exit-zero: 프로세스 종료 상태는 0" in text


def test_exit_zero_on_a_clean_tree_prints_no_note(tmp_path, capsys):
    (tmp_path / "a.txt").write_text("a")
    code, text, _ = run_cli(scan_mod, [str(tmp_path), "--exit-zero", "--lang", "en"], capsys)
    assert code == 0 and "--exit-zero" not in text


def test_exit_zero_does_not_hide_input_errors(tmp_path, capsys):
    code, _, err = run_cli(scan_mod, [str(tmp_path / "missing"), "--exit-zero", "--lang", "en"], capsys)
    assert code == 2 and "path not found" in err


def test_written_plan_is_named_in_the_next_step(site, tmp_path, capsys):
    plan = tmp_path / "p.json"
    code, text, _ = run_cli(scan_mod, [str(site), "--no-git", "--plan-out", str(plan), "--lang", "en"], capsys)
    assert code == 1 and f"Next: apply.py {plan} shows the steps (dry-run)" in text
    code, text, _ = run_cli(scan_mod, [str(site), "--no-git", "--lang", "en"], capsys)
    assert "Next: add --plan-out plan.json" in text
