"""A diagnosis is one scan and one answer (0.1.3).

The trigger evaluation gives an agent 4 turns: load the skill, run the scan, answer. In 0.1.2 an empty folder printed
only "No normalization problems found." and some agents then looked around (ls, git, reading report.json) and ran out
of turns. These tests pin what makes the second command unnecessary:
- the table says what it checked, says when there is nothing to examine and why, and ends with a `Done:` line;
- every value an answer needs (bytes, members, blobs, locations) is printed, so report.json need not be reopened;
- SKILL.md asks for exactly one command before the answer and matches the printed wording.
0.1.4: a table cut at --max-rows says where the rest is instead of claiming completeness, --no-git never claims the
index is clean, and the `Done:` line is a plain statement that also reads naturally for a person.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import _npd as n
from conftest import ROOT, SCRIPTS, make_demo, needs_git, nfc, nfd, run_cli, scan_mod

SKILL_MD = ROOT / "skills" / "nfc-path-doctor" / "SKILL.md"
EVALS = ROOT / "evals"
BASE_SCAN = "python3 <skill-dir>/scripts/scan.py . --exit-zero --out report.json --plan-out plan.json"


def run_base_scan(folder: Path, lang: str, *extra: str) -> subprocess.CompletedProcess:
    """The SKILL.md Step 1 command, run from the user's folder exactly as an agent runs it."""
    argv = [sys.executable, str(SCRIPTS / "scan.py"), ".", "--exit-zero", *extra, "--out", "report.json",
            "--plan-out", "plan.json", "--lang", lang]
    return subprocess.run(argv, cwd=folder, capture_output=True, text=True)


def last_line(text: str) -> str:
    return text.rstrip("\n").splitlines()[-1]


# --- empty or unrelated folder: say so, give the cause, stop ----------------------------------------------------

@pytest.mark.parametrize("lang", ["en", "ko"])
def test_empty_folder_says_nothing_to_examine_and_ends_with_done(tmp_path, lang):
    r = run_base_scan(tmp_path, lang)
    assert r.returncode == 0 and r.stderr == ""
    out = r.stdout
    assert n.msg(lang, "scan_nothing_here") in out
    assert n.msg(lang, "scan_usual_cause") in out
    assert last_line(out) == n.msg(lang, "scan_done_elsewhere")
    assert n.msg(lang, "scan_checked", names=0, names_non_ascii=0, git=n.msg(lang, "scan_git_none"), refs=0,
                 zips=0) in out
    assert "apply.py" not in out                                       # nothing to fix: no fix step is suggested


def test_second_run_in_the_same_folder_still_has_nothing_to_examine(tmp_path):
    """report.json and plan.json of the first run are ASCII names: the folder is still 'nothing here'."""
    assert run_base_scan(tmp_path, "en").returncode == 0
    r = run_base_scan(tmp_path, "en")
    assert n.msg("en", "scan_nothing_here") in r.stdout
    assert "checked: 2 name(s) on disk (0 non-ASCII)" in r.stdout


@needs_git
def test_ascii_only_repository_is_nothing_to_examine(tmp_path):
    repo = tmp_path / "proj"
    repo.mkdir()
    make_demo.init_repo(repo)
    (repo / "readme.txt").write_text("hello\n")
    make_demo.git(repo, "add", "readme.txt")
    r = run_base_scan(tmp_path, "en")
    assert "git index: 1 path(s) (0 non-ASCII)" in r.stdout
    assert n.msg("en", "scan_nothing_here") in r.stdout
    assert last_line(r.stdout) == n.msg("en", "scan_done_elsewhere")


def test_clean_non_ascii_names_are_a_real_clean_result(tmp_path):
    (tmp_path / nfc("한글문서.md")).write_text("hi")
    r = run_base_scan(tmp_path, "en")
    assert "checked: 1 name(s) on disk (1 non-ASCII)" in r.stdout
    assert n.msg("en", "scan_nothing_here") not in r.stdout
    assert last_line(r.stdout) == n.msg("en", "scan_done")


def test_a_given_zip_is_never_nothing_to_examine(tmp_path):
    make_demo.build_names_zip(tmp_path / "a.zip")
    r = run_base_scan(tmp_path, "en", "--zip", "a.zip")
    assert n.msg("en", "scan_nothing_here") not in r.stdout
    assert "1 zip(s)" in r.stdout


# --- findings: everything an answer needs is on screen ----------------------------------------------------------

def hex_values(obj, in_members: bool = False) -> set[str]:
    """Every *_hex value of a finding and the blobs of collision members (the values an answer quotes)."""
    keys = ("path_hex", "nfc_hex", "raw_hex", "disk_hex") + (("blob",) if in_members else ())
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k in keys:
                found.add(v)
            elif k == "members":
                found |= hex_values(v, True)
            else:
                found |= hex_values(v, in_members)
    elif isinstance(obj, list):
        for v in obj:
            found |= hex_values(v, in_members)
    return found


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_findings_table_prints_every_value_of_report_json(tmp_path, lang):
    demo = make_demo.build_demo(tmp_path / "demo")
    r = run_base_scan(demo, lang, "--zip", "archive.zip")
    report = json.loads((demo / "report.json").read_text(encoding="utf-8"))
    assert report["findings"]
    for f in report["findings"]:
        if "location" in f:
            assert f["location"] in r.stdout
        for value in hex_values(f):
            if f["form"] == "collision" and value == f.get("path_hex"):
                continue                                               # a group: its members carry the bytes
            if "raw_hex" in f and value in (f.get("path_hex"), f.get("nfc_hex")):
                continue                                               # legacy zip name: raw bytes + guess are shown
            assert value in r.stdout, (f["kind"], value)
    assert last_line(r.stdout) == n.msg(lang, "scan_done_fix")
    assert n.msg(lang, "scan_nothing_here") not in r.stdout


@needs_git
def test_index_collision_members_and_blobs_are_printed(tmp_path):
    make_demo.build_index_collision(tmp_path / "dup")
    r = run_base_scan(tmp_path, "en")
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    group = next(f for f in report["findings"] if f["kind"] == "index_collision")
    for mem in group["members"]:
        assert f"{mem['path_hex']}  blob={mem['blob']}" in r.stdout
    assert "git index: 2 path(s) (2 non-ASCII)" in r.stdout
    assert last_line(r.stdout) == n.msg("en", "scan_done_fix")


# --- a table cut at --max-rows never claims to be complete (0.1.4) -----------------------------------------------

DONE_COMPLETE_KEYS = ("scan_done", "scan_done_fix", "scan_done_elsewhere")


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_cut_table_names_the_report_file_instead_of_claiming_completeness(tmp_path, lang):
    demo = make_demo.build_demo(tmp_path / "demo")
    r = run_base_scan(demo, lang, "--max-rows", "2")
    assert r.returncode == 0
    total = len(json.loads((demo / "report.json").read_text(encoding="utf-8"))["findings"])
    assert total > 2
    assert last_line(r.stdout) == n.msg(lang, "scan_done_partial", shown=2, total=total, path="report.json")
    assert n.msg(lang, "scan_more_out", n=total - 2, total=total, path="report.json") in r.stdout
    for key in DONE_COMPLETE_KEYS:                                     # never "the complete result" when cut
        assert n.msg(lang, key) not in r.stdout
    assert ("complete result" if lang == "en" else "전체 결과입니다") not in r.stdout


@pytest.mark.parametrize("lang", ["en", "ko"])
def test_cut_table_without_out_says_how_to_get_the_full_list(tmp_path, capsys, lang):
    demo = make_demo.build_demo(tmp_path / "demo")
    code, out, _ = run_cli(scan_mod, [str(demo), "--max-rows", "1", "--lang", lang], capsys)
    total = len(scan_mod.run_scan(str(demo), [], False, False, scan_mod.DEFAULT_REF_EXT, [], False)[0]["findings"])
    assert code in (1, 3)
    assert last_line(out) == n.msg(lang, "scan_done_partial_no_out", shown=1, total=total)
    assert n.msg(lang, "scan_more", n=total - 1) in out


def test_a_table_exactly_at_max_rows_is_complete(tmp_path):
    demo = make_demo.build_demo(tmp_path / "demo")
    total = len(scan_mod.run_scan(str(demo), [], False, False, scan_mod.DEFAULT_REF_EXT, [], False)[0]["findings"])
    r = run_base_scan(demo, "en", "--max-rows", str(total))
    assert last_line(r.stdout) == n.msg("en", "scan_done_fix")
    assert "not shown" not in r.stdout


def test_negative_max_rows_is_a_usage_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        scan_mod.main([str(tmp_path), "--max-rows", "-1", "--lang", "en"])
    assert e.value.code == 2
    assert n.msg("en", "err_max_rows") in capsys.readouterr().err


# --- --no-git: the index was not read, so never say it has no non-ASCII name (0.1.4) ---------------------------

@needs_git
@pytest.mark.parametrize("lang", ["en", "ko"])
def test_no_git_never_claims_the_index_is_clean(tmp_path, lang):
    make_demo.init_repo(tmp_path)
    (tmp_path / nfc("한글.txt")).write_text("x")
    make_demo.git(tmp_path, "add", nfc("한글.txt"))
    (tmp_path / nfc("한글.txt")).unlink()                               # only the index still has the name
    with_git = run_base_scan(tmp_path, lang)
    assert n.msg(lang, "scan_git_checked", n=1, non_ascii=1) in with_git.stdout
    assert n.msg(lang, "scan_nothing_here") not in with_git.stdout
    r = run_base_scan(tmp_path, lang, "--no-git")
    assert n.msg(lang, "scan_git_skipped") in r.stdout
    assert n.msg(lang, "scan_nothing_here") not in r.stdout
    assert n.msg(lang, "scan_git_skipped_note") in r.stdout
    assert last_line(r.stdout) == n.msg(lang, "scan_done")


# --- the Done line reads naturally for a person running the CLI (0.1.4) -----------------------------------------

@pytest.mark.parametrize("key", ["scan_done", "scan_done_fix", "scan_done_elsewhere", "scan_done_partial",
                                 "scan_done_partial_no_out"])
def test_done_lines_are_plain_statements_not_agent_orders(key):
    text = n.msg("en", key, shown=1, total=2, path="report.json").lower()
    for phrase in ("the user", "answer", "do not", "ask "):
        assert phrase not in text, (key, phrase)
    assert text.startswith("done:")
    assert n.msg("ko", key, shown=1, total=2, path="report.json").startswith("끝:")


def test_stats_count_non_ascii_names(tmp_path):
    (tmp_path / nfd("자료.txt")).write_text("x")
    (tmp_path / "plain.txt").write_text("x")
    report, _ = scan_mod.run_scan(str(tmp_path), [], True, False, scan_mod.DEFAULT_REF_EXT, [], False)
    assert report["stats"]["entries"] == 2 and report["stats"]["entries_non_ascii"] == 1
    assert "index_non_ascii" not in report["stats"]                      # --no-git: no index was read


def test_json_mode_stays_pure_json(tmp_path, capsys):
    code, out, err = run_cli(scan_mod, [str(tmp_path), "--json", "--lang", "en"], capsys)
    assert code == 0 and json.loads(out)["exit_code"] == 0
    assert "Done:" not in out and "Done:" not in err


def test_root_defaults_to_the_current_folder(tmp_path, monkeypatch, capsys):
    (tmp_path / nfd("자료.txt")).write_text("x")
    monkeypatch.chdir(tmp_path)
    code, out, _ = run_cli(scan_mod, ["--json", "--lang", "en"], capsys)
    code_dot, out_dot, _ = run_cli(scan_mod, [".", "--json", "--lang", "en"], capsys)
    assert code == code_dot == 1 and out == out_dot
    assert json.loads(out)["root"] in (os.path.realpath(tmp_path), str(tmp_path))


# --- SKILL.md: one command, then the answer -----------------------------------------------------------------------

def skill_body() -> str:
    return SKILL_MD.read_text(encoding="utf-8").split("---", 2)[2]


def section(body: str, title_start: str) -> str:
    m = re.search(r"^## " + re.escape(title_start) + r".*?$(.*?)(?=^## |\Z)", body, re.M | re.S)
    assert m, title_start
    return m.group(1)


def test_diagnosis_needs_one_command_within_the_trigger_turn_budget():
    body = skill_body()
    step1, step2 = section(body, "Step 1"), section(body, "Step 2")
    blocks = re.findall(r"^```[^\n]*\n(.*?)\n```", step1 + step2, re.M | re.S)
    assert [b.strip() for b in blocks] == [BASE_SCAN]                   # one command block before the answer
    assert "python3 " not in step2                                       # the answer step runs nothing
    # load the skill (1) + the scan (1) + the answer (1) fits the smallest max_turns of the trigger cases
    budgets = []
    for prompt in sorted(EVALS.glob("trigger-*/prompt.md")):
        m = re.search(r"^max_turns:\s*(\d+)", prompt.read_text(encoding="utf-8"), re.M)
        budgets.append(int(m.group(1)) if m else 4)
    assert budgets and 1 + 1 + 1 <= min(budgets)


def test_skill_md_forbids_the_follow_up_commands_that_ran_out_of_turns():
    step2 = section(skill_body(), "Step 2")
    for word in ("`Done:`", "`report.json`", "`plan.json`", "references", "`ls`", "`git`", "`cat`", "reply now"):
        assert word in step2, word
    assert "never search the disk" in step2
    assert "one scan and one answer" in skill_body()


def test_skill_md_quotes_the_printed_wording():
    """SKILL.md names lines the agent will see; they must be the script's real English wording."""
    body = skill_body()
    assert "`Nothing here to examine`" in body and n.msg("en", "scan_nothing_here").startswith("Nothing here to examine")
    assert n.msg("en", "scan_done").startswith("Done:") and n.msg("en", "scan_done_elsewhere").startswith("Done:")
    assert n.msg("en", "scan_done_fix").startswith("Done:")
    assert "`bytes:`" in body and n.msg("en", "scan_bytes", hex="").startswith("bytes:")
    # a cut table: SKILL.md quotes the printed words and sends the agent to report.json once (0.1.4)
    assert "`more finding(s) not shown`" in body
    assert "more finding(s) not shown" in n.msg("en", "scan_more", n=1)
    assert "more finding(s) not shown" in n.msg("en", "scan_more_out", n=1, total=2, path="report.json")
    step2 = section(body, "Step 2")
    assert "`findings` in `report.json` once" in step2
