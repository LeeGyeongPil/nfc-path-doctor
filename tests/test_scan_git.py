"""AC-3 (scan part): git index checks."""
from __future__ import annotations

import subprocess

from conftest import SCRIPTS, kinds, make_demo, nfc, nfd, needs_git, run_cli, scan_mod, scan_tree

pytestmark = needs_git


def test_ghost_repo_reports_index_nfd_path(ghost_repo):
    report, plan = scan_tree(ghost_repo)
    assert report["summary"] == {"index_nfd_path": 1, "nfd_name": 1}
    f = next(x for x in report["findings"] if x["kind"] == "index_nfd_path")
    assert f["path_hex"] == nfd("자료.txt").encode().hex()
    assert f["nfc_hex"] == "ec9e90eba38c2e747874"
    assert f["on_disk"] is True and f["mode"] == "100644"
    idx = [a for a in plan["actions"] if a["op"] == "index_rename"][0]
    assert idx["to_hex"] == "ec9e90eba38c2e747874" and idx["blob"] == f["blob"]


def test_index_nfd_with_disk_nfc_is_a_form_mismatch(tmp_path):
    repo = tmp_path / "r"
    make_demo.init_repo(repo)
    make_demo.stage_paths(repo, [(nfd("자료.txt"), b"data\n")])
    make_demo.commit(repo)
    (repo / nfc("자료.txt")).write_bytes(b"data\n")          # the work tree already holds the NFC name
    report, plan = scan_tree(repo)
    mm = next(f for f in report["findings"] if f["kind"] == "index_form_mismatch")
    assert mm["disk_hex"] == nfc("자료.txt").encode().hex() and mm["path_hex"] == nfd("자료.txt").encode().hex()
    assert [a["op"] for a in plan["actions"]] == ["index_rename"]


def test_both_forms_in_the_index_are_a_collision(collision_repo):
    report, plan = scan_tree(collision_repo)
    col = next(f for f in report["findings"] if f["kind"] == "index_collision")
    assert [m["path_hex"] for m in col["members"]] == ["63616665cc812e747874", "636166c3a92e747874"]
    assert report["exit_code"] == 3
    assert plan["actions"] == []                              # never merged automatically
    assert plan["blocked"][0]["kind"] == "index_collision"
    assert {m["blob"] for m in plan["blocked"][0]["members"]} == {m["blob"] for m in col["members"]}
    assert "index_nfd_path" not in kinds(report)               # the collision supersedes the per-path finding


def test_collision_table_lists_both_hex_values(collision_repo, capsys):
    code, out, _ = run_cli(scan_mod, [str(collision_repo), "--lang", "en"], capsys)
    assert code == 3 and "636166c3a92e747874" in out and "63616665cc812e747874" in out
    assert "Collisions are never fixed automatically" in out


def test_no_git_skips_the_index(ghost_repo):
    report, _ = scan_tree(ghost_repo, no_git=True)
    assert "index_nfd_path" not in kinds(report) and report["stats"]["git"].startswith("skipped")


def test_folder_that_is_not_a_repo_is_fine(site):
    report, _ = scan_tree(site)
    assert report["stats"]["git"] == "no repository"


def test_conflict_stage_needs_a_human(tmp_path):
    repo = tmp_path / "r"
    make_demo.init_repo(repo)
    blob = make_demo.git(repo, "hash-object", "-w", "--stdin", data=b"x\n").decode().strip()
    payload = b"".join(f"100644 {blob} {s}\ta.txt\0".encode() for s in (1, 2, 3))
    make_demo.git(repo, "update-index", "-z", "--index-info", data=payload)
    report, plan = scan_tree(repo)
    assert kinds(report) == ["index_conflict_stage"] and report["exit_code"] == 3
    assert report["findings"][0]["stages"] == [1, 2, 3]
    assert plan["blocked"][0]["kind"] == "index_conflict_stage"


def test_subfolder_root_uses_paths_relative_to_it(tmp_path):
    repo = tmp_path / "r"
    make_demo.init_repo(repo)
    make_demo.stage_paths(repo, [("docs/" + nfd("자료.txt"), b"d\n")])
    make_demo.commit(repo)
    (repo / "docs").mkdir()
    (repo / "docs" / nfd("자료.txt")).write_bytes(b"d\n")
    report, plan = scan_tree(repo / "docs")
    f = next(x for x in report["findings"] if x["kind"] == "index_nfd_path")
    assert f["path_hex"] == nfd("자료.txt").encode().hex()                       # relative to the scanned folder
    assert plan["actions"][-1]["to_hex"] == ("docs/" + nfc("자료.txt")).encode().hex()   # but the plan keeps the full path


def test_git_failure_is_an_input_error(ghost_repo, capsys, monkeypatch):
    real = subprocess.run

    def fake(cmd, *a, **kw):
        if "ls-files" in cmd:
            return subprocess.CompletedProcess(cmd, 128, b"", b"fatal: broken index")
        return real(cmd, *a, **kw)

    monkeypatch.setattr(subprocess, "run", fake)
    code, _, err = run_cli(scan_mod, [str(ghost_repo), "--lang", "en"], capsys)
    assert code == 2 and "git failed" in err and "--no-git" in err


def test_missing_git_binary_skips_the_index(ghost_repo, monkeypatch):
    def boom(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    report, _ = scan_tree(ghost_repo)
    assert "index_nfd_path" not in kinds(report) and report["stats"]["git"] == "no repository"


def test_script_dir_is_importable_without_cwd():
    assert (SCRIPTS / "scan.py").exists()


# --- repositories below the scanned folder (e.g. a repository just unpacked from a zip) ---------------------------


def test_nested_repository_collision_is_found_from_the_parent_folder(tmp_path):
    make_demo.build_index_collision(tmp_path / "dup")
    report, plan = scan_tree(tmp_path)                       # the parent folder is not a repository itself
    col = next(f for f in report["findings"] if f["kind"] == "index_collision")
    assert col["repo"] == "dup" and col["repo_hex"] == b"dup".hex()
    assert [m["path_hex"] for m in col["members"]] == ["63616665cc812e747874", "636166c3a92e747874"]   # repo-relative
    assert report["exit_code"] == 3 and report["stats"]["repos"] == ["dup"] and report["stats"]["git"] == "checked"
    assert plan["actions"] == [] and plan["blocked"][0]["repo"] == "dup"


def test_nested_ghost_repository_gets_repo_relative_paths_and_index_action(tmp_path):
    make_demo.build_git_ghost(tmp_path / "proj")
    report, plan = scan_tree(tmp_path)
    assert report["summary"] == {"index_nfd_path": 1, "nfd_name": 1}
    f = next(x for x in report["findings"] if x["kind"] == "index_nfd_path")
    assert f["nfc_hex"] == "ec9e90eba38c2e747874" and f["repo"] == "proj" and f["on_disk"] is True
    fs = next(x for x in report["findings"] if x["kind"] == "nfd_name")
    assert fs["path"].startswith("proj/")                      # file system paths stay relative to the scanned folder
    idx = [a for a in plan["actions"] if a["op"] == "index_rename"][0]
    assert idx["repo_hex"] == b"proj".hex() and idx["to_hex"] == "ec9e90eba38c2e747874"


def test_nested_repository_table_names_the_repository(tmp_path, capsys):
    make_demo.build_git_ghost(tmp_path / "proj")
    code, out, _ = run_cli(scan_mod, [str(tmp_path), "--lang", "en"], capsys)
    assert code == 1 and "(git repository: proj)" in out
    code, out, _ = run_cli(scan_mod, [str(tmp_path), "--lang", "ko"], capsys)
    assert code == 1 and "(git 저장소: proj)" in out


def test_inner_repository_of_a_repository_root_is_not_rescanned(ghost_repo):
    report, _ = scan_tree(ghost_repo)                          # ROOT is the repository: no nested lookup, no repo field
    assert all("repo" not in f for f in report["findings"]) and "repos" not in report["stats"]


def test_broken_dot_git_folder_is_reported_not_silently_clean(tmp_path, capsys):
    (tmp_path / "x" / ".git").mkdir(parents=True)              # not a real repository
    (tmp_path / "x" / "a.txt").write_text("a")
    report, _ = scan_tree(tmp_path)
    assert report["findings"] == [] and report["stats"]["git"] == "no repository"
    assert report["stats"]["repos_unreadable"] == ["x"] and report["exit_code"] == 0
    code, out, _ = run_cli(scan_mod, [str(tmp_path), "--lang", "en"], capsys)
    assert code == 0 and "warning: x has a .git entry that git cannot open" in out and "NOT checked" in out
    code, out, _ = run_cli(scan_mod, [str(tmp_path), "--lang", "ko"], capsys)
    assert code == 0 and "경고: x 에 .git 이 있지만" in out


def test_missing_git_binary_does_not_flag_repositories(tmp_path, monkeypatch):
    make_demo.build_git_ghost(tmp_path / "proj")

    def boom(*a, **kw):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", boom)
    report, _ = scan_tree(tmp_path)
    assert "repos_unreadable" not in report["stats"] and report["stats"]["git"] == "no repository"


# --- round 4: the conditions of the measured agent runs ------------------------------------------------------------
# The eval agents unpacked the repository zip (1) in a folder that belongs to another git repository and (2) in a
# sandbox that refuses to write .git/config, so `python3 -m zipfile -e` stopped after .git/HEAD.


def test_repository_below_a_root_inside_another_repository_is_checked(tmp_path):
    outer = tmp_path / "outer"
    make_demo.init_repo(outer)                                 # the folder the user works in is itself in a repository
    make_demo.build_index_collision(outer / "task" / "dup")
    report, plan = scan_tree(outer / "task")
    col = next(f for f in report["findings"] if f["kind"] == "index_collision")
    assert col["repo"] == "dup" and [m["path_hex"] for m in col["members"]] == ["63616665cc812e747874", "636166c3a92e747874"]
    assert report["summary"] == {"index_collision": 1} and report["exit_code"] == 3
    assert report["stats"]["repos"] == ["dup"] and report["stats"]["git"] == "checked"
    assert plan["git_prefix_hex"] == b"task/".hex() and plan["blocked"][0]["repo"] == "dup"


def test_ghost_below_a_root_inside_another_repository_gets_an_index_action(tmp_path):
    outer = tmp_path / "outer"
    make_demo.init_repo(outer)
    make_demo.build_git_ghost(outer / "task" / "proj")
    report, plan = scan_tree(outer / "task")
    assert report["summary"] == {"index_nfd_path": 1, "nfd_name": 1}
    f = next(x for x in report["findings"] if x["kind"] == "index_nfd_path")
    assert f["nfc_hex"] == "ec9e90eba38c2e747874" and f["repo"] == "proj"
    idx = [a for a in plan["actions"] if a["op"] == "index_rename"]
    assert len(idx) == 1 and idx[0]["repo_hex"] == b"proj".hex()


def test_partly_unpacked_repository_is_reported(tmp_path, capsys):
    outer = tmp_path / "outer"
    make_demo.init_repo(outer)
    for base in (tmp_path / "plain", outer / "task"):
        (base / "dup" / ".git").mkdir(parents=True)
        (base / "dup" / ".git" / "HEAD").write_text("ref: refs/heads/main\n")   # all an unpack stopped at .git/config leaves
        report, _ = scan_tree(base)
        assert report["findings"] == [] and report["stats"]["repos_unreadable"] == ["dup"], base
        code, out, _ = run_cli(scan_mod, [str(base), "--exit-zero", "--lang", "en"], capsys)
        assert code == 0 and "warning: dup has a .git entry that git cannot open" in out


def test_root_with_an_unreadable_dot_git_is_reported_as_dot(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    report, _ = scan_tree(tmp_path)
    assert report["stats"]["repos_unreadable"] == ["."]
