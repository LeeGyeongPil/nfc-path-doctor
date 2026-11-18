"""AC-8, AC-9: SKILL.md has one entry point, the evaluation set is complete, and its graders need the real scripts.

v2 of the plan: five task cases (the git "ghost" fix left the eval set and is covered by tests/test_apply.py), one
script-run pattern for every case, and in every case at least one grader that only the scripts can satisfy. A report
written by hand from the documentation, even with every correct hex value, must fail every case (score < 0.67).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from conftest import ROOT, make_demo, needs_git, nfc, nfd, run_cli, scan_mod

EVALS = ROOT / "evals"
SKILL_DIR = ROOT / "skills" / "nfc-path-doctor"
TASKS = sorted(p.name for p in EVALS.glob("task-*") if p.is_dir())
EXPECTED_TASKS = ["task-error-truncated-zip", "task-git-duplicates", "task-names-compare", "task-site-links",
                  "task-zip-precheck"]
SCRIPT_RAN = r"scripts/(?:scan|show|apply)\.py\b|\b(?:scan|show|apply)\.py\b"
PASS_SCORE = 0.67                                      # a task case passes at this share of matching value graders
# sha256 of the SKILL.md description as measured in round 5 (trigger recall 0.917, false triggers 0.0)
DESCRIPTION_SHA256 = "0fc4742ae9b23cab016de4df9d4246daf7c7c530caceaf0ed63b6c5a9d812179"
BASE_SCAN = "python3 <skill-dir>/scripts/scan.py . --exit-zero --out report.json --plan-out plan.json"


# --- reading the eval files --------------------------------------------------------------------------------------

def front(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    block = text.split("---")[1]
    out = {}
    for line in block.strip().splitlines():
        k, _, v = line.partition(":")
        v = v.strip()
        if v.startswith("'") and v.endswith("'"):
            v = v[1:-1]
        out[k.strip()] = v
    return out


def cases(prefix: str) -> list[Path]:
    return sorted(p for p in EVALS.glob(prefix + "*") if p.is_dir())


def value_graders(case: str) -> list[tuple[str, str, str]]:
    """(file name, regex, target) of each value grader; target is a file name or 'trace'."""
    out = []
    for g in sorted((EVALS / case / "graders").glob("value-*.md")):
        meta = front(g)
        m = re.search(r"path: ([\w.]+)", meta["target"])
        out.append((g.name, meta["pattern"], m.group(1) if m else "trace"))
    return out


def grader_score(case: str, text_by_target: dict[str, str]) -> float:
    """Share of the value graders that match, like the evaluation's case score."""
    hits = [bool(re.search(p, text_by_target.get(t, ""))) for _, p, t in value_graders(case)]
    return sum(hits) / len(hits)


def pattern_hits(case: str, text_by_target: dict[str, str]) -> None:
    for name, pattern, target in value_graders(case):
        assert re.search(pattern, text_by_target[target]), (case, name, pattern)


def docs_text() -> str:
    """Everything an agent can read about the scripts without running them."""
    parts = [(SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")]
    parts += [p.read_text(encoding="utf-8") for p in sorted((SKILL_DIR / "references").glob("*.md"))]
    return "\n".join(parts)


# --- eval set composition (AC-9) ---------------------------------------------------------------------------------

def test_case_counts_meet_the_minimums():
    assert len(cases("trigger-")) >= 10
    assert len(cases("notrigger-")) >= 10
    assert len(cases("notrigger-near-")) >= 5
    assert TASKS == EXPECTED_TASKS                                           # exactly five, no task-git-ghost
    assert not (EVALS / "task-git-ghost").exists() and not (EVALS / "fixtures" / "task-git-ghost").exists()
    assert len(cases("task-error-")) >= 1
    korean = [p for p in cases("trigger-") if re.search(r"[가-힣]", (p / "prompt.md").read_text(encoding="utf-8"))]
    assert len(korean) >= 2


def test_trigger_prompts_do_not_name_the_skill():
    for p in cases("trigger-"):
        body = (p / "prompt.md").read_text(encoding="utf-8").lower()
        assert "nfc-path-doctor" not in body and "skill" not in body and "스킬" not in body


def test_every_task_has_a_fixture_and_value_graders():
    for name in TASKS:
        fx = EVALS / "fixtures" / name
        assert fx.is_dir() and any(fx.iterdir()), name
        assert value_graders(name) and (EVALS / name / "graders" / "script-ran.md").exists()
        for _, pattern, _ in value_graders(name):
            re.compile(pattern)
        for f in fx.rglob("*"):
            assert re.fullmatch(r"[A-Za-z0-9_./ -]+", f.relative_to(fx).as_posix()), f     # harness accepts ASCII paths only


def test_fixtures_are_small_and_have_no_dot_files():
    for f in (EVALS / "fixtures").rglob("*"):
        assert not any(part.startswith(".") for part in f.relative_to(EVALS / "fixtures").parts)
        assert f.stat().st_size < 100_000 or f.is_dir()


def load_builder():
    spec = importlib.util.spec_from_file_location("fixture_build", EVALS / "fixtures" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@needs_git
def test_fixture_builder_is_deterministic(tmp_path):
    build = load_builder().build_all
    a, b = tmp_path / "a", tmp_path / "b"
    files_a, files_b = build(a), build(b)
    assert [p.relative_to(a) for p in files_a] == [p.relative_to(b) for p in files_b]
    assert sorted({p.parent.name for p in files_a}) == EXPECTED_TASKS           # one fixture per remaining case
    for fa, fb in zip(files_a, files_b):
        if fa.suffix == ".zip" and "git" in fa.parent.name:
            # git objects are compressed by the local zlib; compare the zip member names and the normalized index
            with zipfile.ZipFile(fa) as za, zipfile.ZipFile(fb) as zb:
                assert za.namelist() == zb.namelist()
                assert za.read(next(n for n in za.namelist() if n.endswith("/.git/index"))) == \
                    zb.read(next(n for n in zb.namelist() if n.endswith("/.git/index")))
        else:
            assert fa.read_bytes() == fb.read_bytes(), fa.name


def test_a_stub_script_would_fail_the_graders():
    """An empty JSON result matches none of the value patterns."""
    for name in TASKS:
        assert grader_score(name, {"report.json": "{}\n", "names.json": "{}\n", "trace": "{}\n"}) == 0


INVOKED = [
    "python3 /opt/agent/plugins/cache/x/skills/nfc-path-doctor/scripts/scan.py . --exit-zero --out report.json",
    "cd /abs/skills/nfc-path-doctor/scripts && python3 scan.py /task --out /task/report.json",
    'S=/abs/skills/nfc-path-doctor/scripts; python3 "$S/scan.py" . --zip archive.zip',
    "python3 $S/show.py --file names.txt --out names.json",
    "cd scripts; python3 ./apply.py ../plan.json --apply",
    "python3 /abs/skills/nfc-path-doctor/scripts/apply.py --undo undo.json --apply",
]
NOT_INVOKED = ["python3 myscan.py .", "python3 rescan.py", "cat scan.pyc", "python3 -c 'import zipfile'",
               "unzip -l archive.zip", "git ls-files -s"]


def test_script_ran_graders_match_every_invocation_shape():
    """Absolute path, `cd scripts` first, or a shell variable: every way of running the scripts counts."""
    for name in TASKS:
        meta = front(EVALS / name / "graders" / "script-ran.md")
        assert meta["type"] == "tool_used" and meta["tool"] == "Bash" and meta["arm"] == "with-only"
        assert meta["input_match"] == SCRIPT_RAN, name
    for cmd in INVOKED:
        assert re.search(SCRIPT_RAN, json.dumps({"command": cmd})), cmd
    for cmd in NOT_INVOKED:
        assert not re.search(SCRIPT_RAN, json.dumps({"command": cmd})), cmd


# --- running the SKILL.md commands exactly as an agent would from the task folder (AC-8) -------------------------

def skill_md() -> tuple[str, str]:
    """(frontmatter, body) of SKILL.md."""
    _, fm, body = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split("---", 2)
    return fm, body.strip("\n")


def skill_commands() -> list[str]:
    """Every `python3 <skill-dir>/scripts/...` command of SKILL.md (code lines and inline code), in reading order."""
    _, body = skill_md()
    found = [(m.start(), m.group(1)) for m in re.finditer(r"`(python3 <skill-dir>/scripts/[^`]+)`", body)]
    found += [(m.start(), m.group(1)) for m in re.finditer(r"^(python3 <skill-dir>/scripts/.+)$", body, re.M)]
    return [c for _, c in sorted(found)]


def recipe(*must: str) -> str:
    """The first SKILL.md command that contains every given piece."""
    return next(c for c in skill_commands() if all(m in c for m in must))


def run_recipe(cmd: str, cwd: Path, lang: str = "en", env: dict | None = None,
               **subst: str) -> subprocess.CompletedProcess:
    """Run one SKILL.md command in the task folder; <skill-dir> becomes the real skill folder."""
    for k, v in subst.items():
        cmd = cmd.replace(k, v)
    argv = [sys.executable if a == "python3" else a.replace("<skill-dir>", str(SKILL_DIR)) for a in shlex.split(cmd)]
    return subprocess.run(argv + ["--lang", lang], cwd=cwd, capture_output=True, text=True, env=env)


def unpack(zip_path: Path, dest: Path) -> None:
    subprocess.run([sys.executable, "-m", "zipfile", "-e", str(zip_path), str(dest)], check=True, capture_output=True)


def run_task(case: str, folder: Path, lang: str) -> dict[str, str]:
    """Do what the task prompt asks with the SKILL.md commands; returns the graded texts (files and trace)."""
    fx = EVALS / "fixtures" / case
    if case == "task-site-links":
        unpack(fx / "site.zip", folder)
        r = run_recipe(BASE_SCAN, folder, lang)
    elif case == "task-git-duplicates":
        unpack(fx / "dup.zip", folder)
        r = run_recipe(BASE_SCAN, folder, lang)
    elif case == "task-zip-precheck":
        shutil.copy(fx / "archive.zip", folder / "archive.zip")
        r = run_recipe(recipe("scan.py", "--zip FILE.zip"), folder, lang, **{"FILE.zip": "archive.zip"})
    elif case == "task-names-compare":
        shutil.copy(fx / "names.txt", folder / "names.txt")
        r = run_recipe(recipe("show.py --file names.txt"), folder, lang)
    else:
        shutil.copy(fx / "backup.zip", folder / "backup.zip")
        r = run_recipe(recipe("scan.py", "--zip FILE.zip"), folder, lang, **{"FILE.zip": "backup.zip"})
        assert r.returncode == 2                                            # --exit-zero never hides input errors
    if case != "task-error-truncated-zip":
        assert r.returncode == 0, r.stdout + r.stderr
    out = {"trace": r.stdout + r.stderr}
    for name in ("report.json", "names.json"):
        if (folder / name).exists():
            out[name] = (folder / name).read_text(encoding="utf-8")
    return out


def test_skill_md_single_entry_point():
    fm, body = skill_md()
    desc = re.search(r"^description: (.*)$", fm, re.M).group(1)
    assert hashlib.sha256(desc.encode("utf-8")).hexdigest() == DESCRIPTION_SHA256     # triggering is unchanged
    lines = body.splitlines()
    assert len(lines) <= 45, len(lines)
    first_block = re.search(r"^```[^\n]*\n(.*?)\n```", body, re.M | re.S).group(1)
    assert first_block.strip() == BASE_SCAN
    assert skill_commands()[0] == BASE_SCAN                                  # the first command to run is the scan
    assert "&&" not in body                                                  # no chained commands
    assert not [ln for ln in lines if ln.lstrip().startswith("|")]           # no symptom table
    assert "Step 1: always scan first" in body
    section = ""
    for ln in lines:
        if ln.startswith("## "):
            section = ln
        if re.search(r"\b(?:show|apply)\.py\b", ln):
            # show.py and apply.py appear only in conditional sentences or "Only if ..." sections
            assert section.startswith("## Only if") or re.search(r"\bonly\b", ln, re.I), ln
    for script in ("scan", "show", "apply"):
        assert f"`scripts/{script}.py`" in body                                # each script is referenced by path
    for ref in ("references/formats.md", "references/background.md"):
        assert ref in body and (SKILL_DIR / ref).is_file()


def script_help(script: str) -> str:
    return subprocess.run([sys.executable, str(SKILL_DIR / "scripts" / f"{script}.py"), "--help", "--lang", "en"],
                          capture_output=True, text=True).stdout


def test_skill_md_commands_point_at_real_scripts_and_match_the_script_graders():
    cmds = skill_commands()
    assert len(cmds) >= 5
    for c in cmds:
        script = re.match(r"python3 <skill-dir>/scripts/(\w+)\.py", c).group(1)
        assert (SKILL_DIR / "scripts" / f"{script}.py").is_file(), c
        helptext = script_help(script)
        for opt in re.findall(r"(?<!\S)(--[a-z-]+)", c):
            assert opt in helptext, (c, opt)                                 # every option in SKILL.md exists
        assert re.search(SCRIPT_RAN, json.dumps({"command": c.replace("<skill-dir>", "/abs/skill")})), c
    assert recipe("scan.py") == BASE_SCAN                                    # the first command is the scan
    zip_cmd = recipe("scan.py", "--zip FILE.zip")
    assert zip_cmd == BASE_SCAN.replace(" . --exit-zero", " . --exit-zero --zip FILE.zip")


@pytest.mark.parametrize("lang", ["en", "ko"])
@pytest.mark.parametrize("case", EXPECTED_TASKS)
def test_recipe_satisfies_every_value_grader_from_the_task_folder(tmp_path, case, lang):
    out = run_task(case, tmp_path, lang)
    pattern_hits(case, out)
    assert grader_score(case, out) == 1.0


@needs_git
def test_recipe_leaves_the_duplicate_repository_and_the_zip_unchanged(tmp_path):
    dup = tmp_path / "dup"
    dup.mkdir()
    unpack(EVALS / "fixtures" / "task-git-duplicates" / "dup.zip", dup)
    ls = ["git", "-C", str(dup / "dup"), "ls-files", "-z", "--stage"]
    before = subprocess.run(ls, capture_output=True).stdout
    r = run_recipe(BASE_SCAN, dup)
    assert r.returncode == 0 and "636166c3a92e747874" in r.stdout and "63616665cc812e747874" in r.stdout
    assert subprocess.run(ls, capture_output=True).stdout == before                   # the repository is unchanged
    assert json.loads((dup / "plan.json").read_text(encoding="utf-8"))["blocked"]     # the clash waits for a human
    zp = tmp_path / "zip"
    zp.mkdir()
    shutil.copy(EVALS / "fixtures" / "task-zip-precheck" / "archive.zip", zp / "archive.zip")
    digest = hashlib.sha256((zp / "archive.zip").read_bytes()).hexdigest()
    assert run_recipe(recipe("scan.py", "--zip FILE.zip"), zp, **{"FILE.zip": "archive.zip"}).returncode == 0
    assert hashlib.sha256((zp / "archive.zip").read_bytes()).hexdigest() == digest


@pytest.mark.parametrize("case,zname", [("task-site-links", "site.zip"), ("task-git-duplicates", "dup.zip")])
@pytest.mark.parametrize("lang", ["en", "ko"])
def test_graders_also_match_when_the_unpacked_folder_is_scanned(tmp_path, capsys, case, zname, lang):
    """An agent may scan the unpacked folder instead of `.`: the graders must still match."""
    if case == "task-git-duplicates" and shutil.which("git") is None:
        pytest.skip("git is not installed")
    unpack(EVALS / "fixtures" / case / zname, tmp_path)
    report = tmp_path / "report.json"
    code, out, err = run_cli(scan_mod, [str(tmp_path / zname[:-4]), "--out", str(report), "--lang", lang], capsys)
    assert code in (1, 3)
    pattern_hits(case, {"report.json": report.read_text(encoding="utf-8"), "trace": out + err})


@needs_git
def test_recipe_fix_and_undo_from_the_task_folder(tmp_path):
    """The three apply.py commands of SKILL.md on a repository with a decomposed index path (the old ghost case)."""
    repo = make_demo.build_git_ghost(tmp_path / "proj")
    index = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout
    config = (repo / ".git" / "config").read_bytes() if (repo / ".git" / "config").exists() else None
    assert run_recipe(BASE_SCAN, tmp_path).returncode == 0
    dry = run_recipe(recipe("apply.py plan.json"), tmp_path)
    assert dry.returncode == 0 and subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                                                  capture_output=True).stdout == index      # dry-run changes nothing
    fix = run_recipe(recipe("apply.py plan.json --apply"), tmp_path)
    assert fix.returncode == 0 and "re-check: exit 0" in fix.stdout, fix.stdout + fix.stderr
    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-z"], capture_output=True).stdout
    assert status.split(b"\0")[0] == b"R  " + nfc("자료.txt").encode()
    assert ((repo / ".git" / "config").read_bytes() if config is not None else None) == config   # git config unchanged
    undo = run_recipe(recipe("apply.py --undo undo.json --apply"), tmp_path)
    assert undo.returncode == 0, undo.stderr
    assert subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True).stdout == index
    assert nfd("자료.txt").encode() in index


# --- hand-made answers: what a model can produce from the docs without the scripts (AC-9) ------------------------

def documented(value, words: set[str]):
    """Keep the JSON keys an agent can learn from SKILL.md and references (any word that appears there).

    Lists and objects are kept even under an unknown key (the forger guesses some container name), only undocumented
    scalar fields are dropped, so the forgery is as strong as the docs allow."""
    if isinstance(value, dict):
        return {k: documented(v, words) for k, v in value.items() if k in words or isinstance(v, (dict, list))}
    if isinstance(value, list):
        return [documented(v, words) for v in value]
    return value


# command output an agent sees when it checks names by hand (git, unzip, grep, its own Python), per case
HAND_TRACE = {
    "task-site-links": (
        "site/README.md:3:![logo](이미지/로고.png)\n"
        "site/README.md:3:9 ref_mismatch 이미지/로고.png -> on disk e1848be185b5e18486e185b5e1848ce185b52f"
        "e18485e185a9e18480e185a92e706e67\n"
        "nfd_name: 3, ref_mismatch: 1\n"),
    "task-git-duplicates": (
        '100644 f719efd430d52bcfc8566a43b2eb655688d38871 0\t"cafe\\314\\201.txt"\n'
        '100644 5626abf0f72e58d7a153368ba57db4c673c0e171 0\t"caf\\303\\251.txt"\n'
        "index_collision: 63616665cc812e747874 blob f719efd430d52bcfc8566a43b2eb655688d38871 vs "
        "636166c3a92e747874 blob 5626abf0f72e58d7a153368ba57db4c673c0e171\n"),
    "task-zip-precheck": (
        "flag=0x0 raw_hex=c7d1b1db2e747874 cp949=한글.txt\n"
        "flag=0x800 nfd e18487e185a9e18480e185a9e18489e185a52f\n"
        "zip_nfd_name: 2, zip_legacy_name: 1 (raw_hex c7d1b1db2e747874, guess cp949)\n"),
    "task-names-compare": "same_bytes: False\nsame_nfc: True\nU+0063 U+0061 U+0066 U+0065 U+0301\n",
    "task-error-truncated-zip": (
        "Traceback (most recent call last):\n  ...\nzipfile.BadZipFile: File is not a zip file\n"
        "End-of-central-directory signature not found.\n"
        "error: backup.zip is a broken or truncated zip, so its names cannot be checked\n"),
}


def forged(case: str, real: dict[str, str]) -> dict[str, str]:
    """A careful hand-made answer: the real JSON reduced to documented keys (so every hex value is correct), sorted
    keys and two-space indent like the scripts, and a trace with the forged files and hand-run command output."""
    words = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", docs_text()))
    out = {}
    for name in ("report.json", "names.json"):
        if name in real:
            data = documented(json.loads(real[name]), words)
            out[name] = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    out["trace"] = HAND_TRACE[case] + "".join(out.values())
    return out


@pytest.mark.parametrize("case", EXPECTED_TASKS)
def test_hand_written_reports_fail_every_task(tmp_path, case):
    """Mutation check: with the scripts replaced by fakes, a hand-made answer must not pass any task case."""
    real = run_task(case, tmp_path, "en")
    assert grader_score(case, real) == 1.0
    score = grader_score(case, forged(case, real))
    assert score < PASS_SCORE, (case, score)
    assert score <= 0.5, (case, score)                 # a clear margin: no rounding of 2/3 can turn it into a pass
    assert grader_score(case, {"report.json": "{}\n", "names.json": "{}\n", "trace": "{}\n"}) == 0


@pytest.mark.parametrize("case", EXPECTED_TASKS)
def test_every_task_has_a_script_only_grader(tmp_path, case):
    """At least one grader matches the scripts' output in both languages, but neither the docs nor a forgery."""
    docs = docs_text()
    real = {}
    for lang in ("en", "ko"):
        (tmp_path / lang).mkdir()
        real[lang] = run_task(case, tmp_path / lang, lang)
    fake = forged(case, real["en"])
    only = [name for name, pattern, target in value_graders(case)
            if not re.search(pattern, docs) and not re.search(pattern, fake.get(target, ""))
            and all(re.search(pattern, real[lang][target]) for lang in real)]
    assert only, case


# --- round 4: the git task case under the conditions of the measured agent runs ---------------------------------
# The agent sandbox refuses to write .git/config and .git/hooks/* below the working folder (so
# `python3 -m zipfile -e` would stop early), and the task folder may lie inside another git repository.

SANDBOX_REFUSED = re.compile(r"(^|/)\.git/(config$|hooks/)")


def sandbox_unpack(zip_path: Path, dest: Path) -> list[str]:
    """Unpack in member order like `python3 -m zipfile -e`, which stops at the first member the sandbox refuses."""
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if SANDBOX_REFUSED.search(info.filename):
                return [info.filename]
            zf.extract(info, dest)
    return []


def git_env(precompose: str) -> dict[str, str]:
    """Environment with core.precomposeunicode forced on or off (a user's global git config may set either)."""
    return {**os.environ, "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.precomposeunicode",
            "GIT_CONFIG_VALUE_0": precompose}


def test_git_fixture_has_no_member_an_agent_sandbox_refuses(tmp_path):
    zp = EVALS / "fixtures" / "task-git-duplicates" / "dup.zip"
    with zipfile.ZipFile(zp) as zf:
        assert not [m for m in zf.namelist() if SANDBOX_REFUSED.search(m)]
    assert sandbox_unpack(zp, tmp_path) == []
    if shutil.which("git"):
        assert scan_mod.n.git_prefix(str(tmp_path / "dup")) == b""                 # git opens it without .git/config


def test_sandbox_unpack_reproduces_the_stop_of_an_old_style_zip(tmp_path):
    old = tmp_path / "old.zip"
    with zipfile.ZipFile(old, "w") as zf:
        for name in ("dup/.git/HEAD", "dup/.git/config", "dup/.git/index"):
            zf.writestr(name, b"x")
    assert sandbox_unpack(old, tmp_path / "out") == ["dup/.git/config"]
    assert sorted(p.name for p in (tmp_path / "out" / "dup" / ".git").iterdir()) == ["HEAD"]


@needs_git
@pytest.mark.parametrize("precompose", ["true", "false"])
def test_recipe_git_duplicates_in_a_task_folder_inside_another_repository(tmp_path, precompose):
    outer = tmp_path / "outer"
    make_demo.init_repo(outer)
    task = outer / "work" / "task-git-duplicates"
    task.mkdir(parents=True)
    assert sandbox_unpack(EVALS / "fixtures" / "task-git-duplicates" / "dup.zip", task) == []
    env = git_env(precompose)
    ls = ["git", "-C", str(task / "dup"), "ls-files", "-z", "--stage"]
    before = subprocess.run(ls, capture_output=True, env=env).stdout
    r = run_recipe(BASE_SCAN, task, env=env)
    assert r.returncode == 0 and "index_collision" in r.stdout and "(git repository: dup)" in r.stdout, r.stdout
    pattern_hits("task-git-duplicates", {"report.json": (task / "report.json").read_text(encoding="utf-8"),
                                         "trace": r.stdout + r.stderr})
    assert subprocess.run(ls, capture_output=True, env=env).stdout == before          # repository left unchanged
