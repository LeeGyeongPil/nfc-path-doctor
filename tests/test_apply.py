"""AC-5 (and the apply part of AC-3): dry-run, apply, stale plans, failure rollback and undo."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import SCRIPTS, apply_mod, make_demo, needs_git, nfc, nfd, run_cli, scan_mod, scan_tree, snapshot


def make_plan(root: Path, out: Path, *extra: str) -> Path:
    assert scan_mod.main([str(root), "--plan-out", str(out), "--out", str(out) + ".report.json", *extra]) in (0, 1, 3)
    return out


def git_state(repo: Path) -> dict[str, bytes]:
    return {p: (repo / ".git" / p).read_bytes() for p in ("config", "HEAD")}


def test_dry_run_changes_nothing(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    before = snapshot(site)
    code, out, _ = run_cli(apply_mod, [str(plan), "--lang", "en"], capsys)
    assert code == 0 and "dry-run: 3 step(s) would run, nothing was changed" in out
    assert "--apply" in out
    assert snapshot(site) == before
    assert not (tmp_path / "undo.json").exists()


@needs_git
def test_dry_run_leaves_git_index_and_config_untouched(ghost_repo, tmp_path, capsys):
    plan = make_plan(ghost_repo, tmp_path / "plan.json")
    index = (ghost_repo / ".git" / "index").read_bytes()
    before = snapshot(ghost_repo)
    code, _, _ = run_cli(apply_mod, [str(plan), "--lang", "en"], capsys)
    assert code == 0
    assert (ghost_repo / ".git" / "index").read_bytes() == index and snapshot(ghost_repo) == before


def test_apply_renames_deepest_first_and_rescan_is_clean(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    ops = json.loads(plan.read_text())["actions"]
    assert ops[0]["op"] == "fs_rename" and "/" in ops[0]["path"]      # the file inside the folder goes before the folder
    code, out, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    assert "done: 3 step(s) applied" in out and "re-check: exit 0, no findings" in out
    names = sorted(os.listdir(os.fsencode(site)))
    assert nfc("이미지").encode() in names and nfd("이미지").encode() not in names
    assert os.listdir(os.fsencode(site / nfc("이미지"))) == [nfc("로고.png").encode()]
    assert scan_tree(site, no_git=True)[0]["exit_code"] == 0
    assert (tmp_path / "undo.json").exists()


@needs_git
def test_git_index_rename_gives_a_clean_rename_without_config_change(ghost_repo, tmp_path, capsys):
    config = git_state(ghost_repo)
    plan = make_plan(ghost_repo, tmp_path / "plan.json")
    code, out, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    status = subprocess.run(["git", "-C", str(ghost_repo), "status", "--porcelain", "-z"], capture_output=True).stdout
    entries = [e for e in status.split(b"\0") if e]
    assert entries[0][:3] == b"R  " and entries[0][3:] == nfc("자료.txt").encode()      # index path is NFC now
    assert nfd("자료.txt").encode() in entries                                          # the old path is the rename source
    ls = subprocess.run(["git", "-C", str(ghost_repo), "ls-files", "-z"], capture_output=True).stdout
    assert ls == bytes.fromhex("ec9e90eba38c2e747874") + b"\0"
    assert git_state(ghost_repo) == config
    assert "re-check: exit 0" in out


@needs_git
def test_collision_stays_untouched_while_other_names_are_fixed(tmp_path, capsys):
    repo = make_demo.build_index_collision(tmp_path / "r")
    make_demo.stage_paths(repo, [(nfd("자료.txt"), b"d\n")])
    (repo / nfd("자료.txt")).write_bytes(b"d\n")
    plan = make_plan(repo, tmp_path / "plan.json")
    data = json.loads(plan.read_text())
    assert [b["kind"] for b in data["blocked"]] == ["index_collision"]
    code, out, _ = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0 and "blocked (not touched" in out
    listed = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout.split(b"\0")
    assert nfc("café.txt").encode() in listed and nfd("café.txt").encode() in listed      # both entries survive
    assert nfc("자료.txt").encode() in listed
    assert scan_tree(repo)[0]["exit_code"] == 3                                           # still needs a human


def test_references_are_rewritten_and_undo_restores_exact_bytes(tmp_path, capsys):
    (tmp_path / nfc("가.txt")).write_text("x")
    (tmp_path / nfc("나.txt")).write_text("x")
    doc = tmp_path / "doc.md"
    doc.write_bytes(("# t\n" + nfd("가.txt") + " and " + nfd("나.txt") + "\n" + "end\r\n").encode())
    original = doc.read_bytes()
    plan = make_plan(tmp_path, tmp_path.parent / (tmp_path.name + "-plan.json"), "--no-git")
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--undo-out", str(tmp_path.parent / "u.json"), "--lang", "en"], capsys)
    assert code == 0, err
    assert doc.read_bytes() == ("# t\n" + nfc("가.txt") + " and " + nfc("나.txt") + "\n" + "end\r\n").encode()
    code, out, err = run_cli(apply_mod, ["--undo", str(tmp_path.parent / "u.json"), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    assert doc.read_bytes() == original


def test_undo_restores_every_name_byte_for_byte(site, tmp_path, capsys):
    before = snapshot(site)
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    assert run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)[0] == 0
    assert snapshot(site) != before
    code, _, err = run_cli(apply_mod, ["--undo", str(tmp_path / "undo.json"), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    assert snapshot(site) == before


def test_undo_dry_run_changes_nothing(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    after = snapshot(site)
    code, out, _ = run_cli(apply_mod, ["--undo", str(tmp_path / "undo.json"), "--lang", "en"], capsys)
    assert code == 0 and "dry-run" in out and snapshot(site) == after


@needs_git
def test_undo_restores_the_git_index(ghost_repo, tmp_path, capsys):
    index = subprocess.run(["git", "-C", str(ghost_repo), "ls-files", "-z"], capture_output=True).stdout
    plan = make_plan(ghost_repo, tmp_path / "plan.json")
    run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    code, _, err = run_cli(apply_mod, ["--undo", str(tmp_path / "undo.json"), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    assert subprocess.run(["git", "-C", str(ghost_repo), "ls-files", "-z"], capture_output=True).stdout == index


def test_stale_when_a_referenced_file_changed(tmp_path, capsys):
    (tmp_path / nfc("가.txt")).write_text("x")
    doc = tmp_path / "doc.md"
    doc.write_bytes((nfd("가.txt") + "\n").encode())
    plan = make_plan(tmp_path, tmp_path.parent / (tmp_path.name + "-p.json"), "--no-git")
    doc.write_bytes((nfd("가.txt") + " edited\n").encode())
    before = snapshot(tmp_path)
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 1 and "plan is stale" in err and "content changed" in err
    assert snapshot(tmp_path) == before                                                  # nothing was touched


def test_stale_when_a_name_disappeared(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    (site / nfd("ガイド.md")).unlink()
    before = snapshot(site)
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 1 and "no longer exists" in err and "Run scan.py again" in err
    assert snapshot(site) == before


def test_stale_when_the_target_name_appeared(tmp_path, capsys, coexist):
    if not coexist:
        pytest.skip("on APFS the NFC name is the same file; this check matters on Linux")
    (tmp_path / nfd("가.txt")).write_text("x")
    plan = make_plan(tmp_path, tmp_path.parent / (tmp_path.name + "-p.json"), "--no-git")
    (tmp_path / nfc("가.txt")).write_text("other")
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 1 and "already exists" in err


@needs_git
def test_stale_when_the_index_changed(ghost_repo, tmp_path, capsys):
    plan = make_plan(ghost_repo, tmp_path / "plan.json")
    entry = [a for a in json.loads(plan.read_text())["actions"] if a["op"] == "index_rename"][0]
    zeros = "0" * len(entry["blob"])
    # drop the entry through stdin (argument paths would be precomposed by git on macOS)
    make_demo.git(ghost_repo, "update-index", "-z", "--index-info",
                  data=f"0 {zeros}\t".encode() + bytes.fromhex(entry["from_hex"]) + b"\0")
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 1 and "index entry changed or missing" in err


def test_failure_in_the_middle_rolls_back(site, tmp_path, capsys, monkeypatch):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    before = snapshot(site)
    real, calls = os.rename, {"n": 0}

    def flaky(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("disk exploded")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(apply_mod.os, "rename", flaky)
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    monkeypatch.undo()
    assert code == 4 and "step failed: disk exploded" in err and "rolled back (2 step(s))" in err
    assert snapshot(site) == before
    assert not (tmp_path / "undo.json").exists()


def test_failed_rollback_leaves_an_undo_file(site, tmp_path, capsys, monkeypatch):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    real, calls = os.rename, {"n": 0}

    def flaky(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] >= 2:
            raise OSError("still broken")
        return real(src, dst, *a, **kw)

    monkeypatch.setattr(apply_mod.os, "rename", flaky)
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    monkeypatch.undo()
    assert code == 4 and "rollback of a step failed" in err
    undo = json.loads((tmp_path / "undo.json").read_text())
    assert undo["target"] == "undo" and len(undo["actions"]) == 1


def test_file_system_that_keeps_the_old_form_is_an_error(site, tmp_path, capsys, monkeypatch):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    monkeypatch.setattr(apply_mod.os, "rename", lambda *a, **kw: None)         # pretends to rename (HFS+ style)
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    monkeypatch.undo()
    assert code == 4 and "did not keep the NFC name" in err


def test_empty_plan_is_nothing_to_do(tmp_path, capsys):
    (tmp_path / "ok.txt").write_text("x")
    plan = make_plan(tmp_path, tmp_path.parent / (tmp_path.name + "-e.json"), "--no-git")
    code, out, _ = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0 and "nothing to do" in out


def test_invalid_plan_files_are_input_errors(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    for text, needle in [("not json", "not valid JSON"), ("[]", "unsupported or missing version"),
                         (json.dumps({"version": 1, "target": "NFD", "root_hex": "00", "actions": []}), "target must be"),
                         (json.dumps({"version": 1, "target": "NFC", "root_hex": "zz", "actions": []}), "not valid hex"),
                         (json.dumps({"version": 1, "target": "NFC", "root_hex": "00", "actions": [{"op": "rm"}]}), "unknown op"),
                         (json.dumps({"version": 1, "target": "NFC", "root_hex": "00", "actions": [{"op": "fs_rename"}]}), "must be a hex string"),
                         (json.dumps({"version": 1, "target": "NFC", "root_hex": "00", "actions": [{"op": "index_rename", "from_hex": "00", "to_hex": "00"}]}), "needs mode and blob"),
                         (json.dumps({"version": 1, "target": "NFC", "root_hex": "00", "actions": [{"op": "ref_rewrite", "file_hex": "00"}]}), "needs sha256")]:
        bad.write_text(text)
        code, _, err = run_cli(apply_mod, [str(bad), "--lang", "en"], capsys)
        assert code == 2 and needle in err, (text, err)


def test_missing_plan_and_wrong_argument_combinations(tmp_path, capsys):
    assert run_cli(apply_mod, [str(tmp_path / "none.json"), "--lang", "en"], capsys)[0] == 2
    code, _, err = run_cli(apply_mod, ["--lang", "en"], capsys)
    assert code == 2 and "exactly one of PLAN or --undo" in err
    code, _, err = run_cli(apply_mod, ["a.json", "--undo", "b.json", "--lang", "en"], capsys)
    assert code == 2


def test_plan_for_a_missing_root_is_stale(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    for p in sorted(site.rglob("*"), reverse=True):
        p.rmdir() if p.is_dir() else p.unlink()
    site.rmdir()
    code, _, err = run_cli(apply_mod, [str(plan), "--lang", "en"], capsys)
    assert code == 1 and "root folder is missing" in err


def test_korean_dry_run_output(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json", "--no-git")
    code, out, _ = run_cli(apply_mod, [str(plan), "--lang", "ko"], capsys)
    assert code == 0 and "dry-run: 3단계를 실행할 예정이며" in out and "이름 변경" in out


def test_cli_runs_as_a_script(site, tmp_path):
    plan = tmp_path / "plan.json"
    r = subprocess.run([sys.executable, str(SCRIPTS / "scan.py"), str(site), "--no-git", "--plan-out", str(plan), "--lang", "en"],
                       capture_output=True, text=True)
    assert r.returncode == 1
    r = subprocess.run([sys.executable, str(SCRIPTS / "apply.py"), str(plan), "--apply", "--lang", "en"],
                       capture_output=True, text=True)
    assert r.returncode == 0 and "re-check: exit 0" in r.stdout


# --- a repository below the scanned folder ---------------------------------------------------------------------


@needs_git
def test_nested_repository_fix_from_the_parent_folder_and_undo(tmp_path, capsys):
    work = tmp_path / "work"
    repo = make_demo.build_git_ghost(work / "proj")
    config = git_state(repo)
    index = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout
    plan = tmp_path / "plan.json"
    assert scan_mod.main([str(work), "--plan-out", str(plan), "--exit-zero"]) == 0
    code, out, err = run_cli(apply_mod, [str(plan), "--lang", "en"], capsys)
    assert code == 0 and "(git repository: proj)" in out                             # dry-run names the repository
    code, out, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z"], capture_output=True).stdout
    entries = [e for e in status.split(b"\0") if e]
    assert entries[0] == b"R  " + nfc("자료.txt").encode() and nfd("자료.txt").encode() in entries
    assert git_state(repo) == config and "re-check: exit 0" in out
    code, _, err = run_cli(apply_mod, ["--undo", str(tmp_path / "undo.json"), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    assert subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout == index


@needs_git
def test_nested_repository_inside_an_outer_repository_fix_and_undo(tmp_path, capsys):
    """Round 4 regression: the scanned folder belongs to another repository (a zip unpacked in a project folder).

    Before, the scan only read the outer index, missed the inner repository and planned a plain file rename; after
    --apply git showed a new untracked NFC file next to the NFD index entry (the ghost the user wanted gone).
    """
    outer = tmp_path / "outer"
    make_demo.init_repo(outer)
    work = outer / "work"
    repo = make_demo.build_git_ghost(work / "proj")
    outer_config = git_state(outer)
    index = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout
    plan = work / "plan.json"
    assert scan_mod.main([str(work), "--plan-out", str(plan), "--exit-zero"]) == 0
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert [(a["op"], a.get("repo_hex")) for a in data["actions"]] == [("fs_rename", None), ("index_rename", b"proj".hex())]
    code, out, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0 and "re-check: exit 0" in out, err
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z"], capture_output=True).stdout
    assert status.split(b"\0")[0] == b"R  " + nfc("자료.txt").encode()                  # a rename, not an untracked file
    assert subprocess.run(["git", "-C", str(outer), "ls-files", "-z"], capture_output=True).stdout == b""   # outer untouched
    assert git_state(outer) == outer_config
    code, _, err = run_cli(apply_mod, ["--undo", str(work / "undo.json"), "--apply", "--lang", "en"], capsys)
    assert code == 0, err
    assert subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout == index


@needs_git
def test_nested_repository_whose_folder_name_is_decomposed(tmp_path, capsys):
    work = tmp_path / "work"
    repo = make_demo.build_git_ghost(work / nfd("저장소"))                           # the repository folder is NFD too
    plan = tmp_path / "plan.json"
    assert scan_mod.main([str(work), "--plan-out", str(plan), "--exit-zero"]) == 0
    data = json.loads(plan.read_text(encoding="utf-8"))
    assert [a["op"] for a in data["actions"]] == ["fs_rename", "fs_rename", "index_rename"]
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 0, err                                     # the index step finds the renamed (NFC) repository folder
    new_repo = work / nfc("저장소")
    ls = subprocess.run(["git", "-C", str(new_repo), "ls-files", "-z"], capture_output=True).stdout
    assert ls == bytes.fromhex("ec9e90eba38c2e747874") + b"\0"
    assert repo.name.encode() != nfc("저장소").encode()


@needs_git
def test_stale_when_a_nested_index_changed(tmp_path, capsys):
    work = tmp_path / "work"
    repo = make_demo.build_git_ghost(work / "proj")
    plan = tmp_path / "plan.json"
    scan_mod.main([str(work), "--plan-out", str(plan), "--exit-zero"])
    make_demo.git(repo, "rm", "--cached", "-q", "-r", ".")
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 1 and "index entry changed or missing" in err


def test_plan_with_a_bad_repo_hex_is_invalid(tmp_path, capsys):
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"version": 1, "target": "NFC", "root_hex": os.fsencode(str(tmp_path)).hex(),
                                "actions": [{"op": "index_rename", "from_hex": "61", "to_hex": "62", "mode": "100644",
                                             "blob": "0" * 40, "repo_hex": "zz"}]}))
    code, _, err = run_cli(apply_mod, [str(plan), "--lang", "en"], capsys)
    assert code == 2 and "repo_hex" in err


def test_plan_problems_are_fully_localized_in_korean(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    for text in ["{}", "not json", json.dumps({"version": 1, "target": "NFD", "root_hex": "00", "actions": []}),
                 json.dumps({"version": 1, "target": "NFC", "root_hex": "zz", "actions": []}),
                 json.dumps({"version": 1, "target": "NFC", "root_hex": "00", "actions": [{"op": "fs_rename"}]}),
                 json.dumps({"version": 1, "target": "NFC", "root_hex": "00", "actions": [{"op": "rm"}]})]:
        bad.write_text(text)
        code, _, err = run_cli(apply_mod, [str(bad), "--lang", "ko"], capsys)
        assert code == 2 and err.startswith("오류"), err
        for word in ("unsupported", "not valid", "must be", "unknown op", "needs"):
            assert word not in err, err


def test_plan_paths_leaving_the_root_are_rejected(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    for hexval in (b"/etc".hex(), b"../x".hex(), b"a/../../x".hex()):
        plan = {"version": 1, "target": "NFC", "root_hex": b"/tmp".hex(),
                "actions": [{"op": "fs_rename", "parent_hex": hexval, "from_hex": b"a".hex(), "to_hex": b"b".hex()}]}
        bad.write_text(json.dumps(plan))
        for lang in ("en", "ko"):
            code, _, err = run_cli(apply_mod, [str(bad), "--lang", lang], capsys)
            assert code == 2 and "parent_hex" in err, err


def test_scan_refuses_to_overwrite_a_foreign_json_file(tmp_path, capsys):
    (tmp_path / nfd("자료")).mkdir()
    mine = tmp_path / "plan.json"
    mine.write_text('{"my": "project plan"}')
    for lang, needle in (("en", "refusing to overwrite"), ("ko", "덮어쓰지 않습니다")):
        code, _, err = run_cli(scan_mod, [str(tmp_path), "--plan-out", str(mine), "--lang", lang], capsys)
        assert code == 2 and needle in err
        assert mine.read_text() == '{"my": "project plan"}'
    # the tool's own files may be replaced again and again
    own = tmp_path / "out" / "report.json"
    own.parent.mkdir()
    for _ in range(2):
        assert run_cli(scan_mod, [str(tmp_path), "--out", str(own), "--exit-zero"], capsys)[0] == 0


def test_apply_refuses_a_foreign_undo_file_before_renaming(site, tmp_path, capsys):
    plan = make_plan(site, tmp_path / "plan.json")
    undo = tmp_path / "undo.json"
    undo.write_text("keep me")
    before = snapshot(site)
    code, _, err = run_cli(apply_mod, [str(plan), "--apply", "--lang", "en"], capsys)
    assert code == 2 and "refusing to overwrite" in err
    assert undo.read_text() == "keep me" and snapshot(site) == before
