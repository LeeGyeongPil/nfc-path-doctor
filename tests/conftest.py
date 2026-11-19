"""Shared test helpers. Every NFD name is built with unicodedata.normalize at run time (never typed in source)."""
from __future__ import annotations

import os
import shutil
import sys
import unicodedata
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "skills" / "nfc-path-doctor" / "scripts"
sys.dont_write_bytecode = True   # keep evals/fixtures free of __pycache__ (the fixture lint rejects extra folders)
for p in (str(SCRIPTS), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)

import apply as apply_mod  # noqa: E402
import make_demo  # noqa: E402
import scan as scan_mod  # noqa: E402
import show as show_mod  # noqa: E402


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def fs_distinguishes_forms(base: Path) -> bool:
    """True when this file system keeps NFC and NFD names as two different files (ext4, NTFS, ...). APFS does not."""
    probe = base / "probe-dir"
    probe.mkdir(exist_ok=True)
    a, b = probe / nfc("café"), probe / nfd("café")
    a.write_text("a")
    try:
        with open(b, "x") as fh:
            fh.write("b")
    except FileExistsError:
        return False
    finally:
        shutil.rmtree(probe, ignore_errors=True)
    return True


@pytest.fixture(scope="session", autouse=True)
def clean_git_config():
    """Run every git call (tests and scripts) as on a fresh CI runner: no global or system git config.

    A developer's global config (core.precomposeunicode, user identity, hooks, init.defaultBranch) must not decide
    whether a test passes; GitHub runners have none of it.
    """
    saved = {k: os.environ.get(k) for k in ("GIT_CONFIG_GLOBAL", "GIT_CONFIG_NOSYSTEM")}
    os.environ["GIT_CONFIG_GLOBAL"] = os.devnull       # 사용자 전역 설정(~/.gitconfig) 무시
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"            # 시스템 설정(/etc/gitconfig) 무시
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture(scope="session")
def coexist(tmp_path_factory) -> bool:
    return fs_distinguishes_forms(tmp_path_factory.mktemp("probe"))


needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def scan_tree(root: Path, zips: list[str] | None = None, **kw):
    """Run the scanner in-process; returns (report, plan)."""
    return scan_mod.run_scan(str(root), zips or [], kw.get("no_git", False), kw.get("no_refs", False),
                             kw.get("ref_ext", scan_mod.DEFAULT_REF_EXT), kw.get("exclude", []), kw.get("casefold", False))


def kinds(report: dict) -> list[str]:
    return [f["kind"] for f in report["findings"]]


def run_cli(module, argv: list[str], capsys, env: dict | None = None, monkeypatch=None):
    """Call module.main(argv) and return (exit code, stdout, stderr)."""
    if monkeypatch is not None:
        for k in ("NFC_PATH_DOCTOR_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
            monkeypatch.delenv(k, raising=False)
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
    code = module.main(argv)
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def snapshot(root: Path) -> dict[bytes, bytes | None]:
    """Every path (as bytes) below root with its content, for before/after comparisons."""
    snap: dict[bytes, bytes | None] = {}
    rb = os.fsencode(root)
    for dirpath, dirnames, filenames in os.walk(rb):
        for d in dirnames:
            snap[os.path.relpath(os.path.join(dirpath, d), rb)] = None
        for f in filenames:
            full = os.path.join(dirpath, f)
            snap[os.path.relpath(full, rb)] = Path(os.fsdecode(full)).read_bytes()
    return snap


@pytest.fixture()
def site(tmp_path) -> Path:
    return make_demo.build_site(tmp_path / "site")


@pytest.fixture()
def ghost_repo(tmp_path) -> Path:
    return make_demo.build_git_ghost(tmp_path / "ghost")


@pytest.fixture()
def collision_repo(tmp_path) -> Path:
    return make_demo.build_index_collision(tmp_path / "dup")


# ---------------------------------------------------------------------------
# GitHub Actions annotations
# ---------------------------------------------------------------------------
# A run's annotations are listed on the run page and by `gh run view` without downloading the job log, so a failing
# CI job says which test failed and why, and the ubuntu job says whether the Linux-only tests really ran (not skipped).

LINUX_ONLY_SKIP_WORDS = ("APFS", "case-insensitive")      # skip reasons of the tests that only run on Linux


def gh_escape(text: str, prop: bool = False) -> str:
    """Escape a value for a workflow command (::error ...::message); properties also escape ':' and ','."""
    text = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return text.replace(":", "%3A").replace(",", "%2C") if prop else text


def gh_command(kind: str, message: str, **props: object) -> str:
    """One workflow command line, e.g. ::error file=tests/x.py,line=3,title=t::message."""
    args = ",".join(f"{k}={gh_escape(str(v), prop=True)}" for k, v in props.items() if v is not None)
    return f"::{kind}{' ' + args if args else ''}::{gh_escape(message)}"


def _reports(stats: dict, key: str) -> list:
    return [r for r in stats.get(key, []) if getattr(r, "when", None) is not None]   # test reports only, no warnings


def _failure_message(report) -> str:
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    text = getattr(crash, "message", None) or str(getattr(report, "longrepr", "") or "failed")
    return text.strip()[:1000]


def _skip_reason(report) -> str:
    lr = getattr(report, "longrepr", None)
    reason = lr[2] if isinstance(lr, tuple) and len(lr) == 3 else str(lr or "")
    return reason.removeprefix("Skipped: ")


def ci_annotations(stats: dict, platform: str, python: str) -> list[str]:
    """Workflow commands for a finished test session: one ::error per failure, one ::notice summary, and a
    ::warning if a Linux-only test was skipped on Linux (the file system there should hold both name forms)."""
    lines = []
    for key in ("failed", "error"):
        for r in _reports(stats, key):
            path, lineno, _ = getattr(r, "location", (None, None, None))
            lines.append(gh_command("error", _failure_message(r), file=path,
                                    line=None if lineno is None else lineno + 1, title=f"{key}: {r.nodeid}"))
    skipped = [(r.nodeid, _skip_reason(r)) for r in _reports(stats, "skipped")]
    counts = {k: len(_reports(stats, k)) for k in ("passed", "failed", "error", "skipped")}
    summary = f"{platform} Python {python}: " + ", ".join(f"{v} {k}" for k, v in counts.items())
    detail = "".join(f"\nSKIPPED {node}: {why}" for node, why in skipped)
    lines.append(gh_command("notice", summary + detail, title="pytest summary"))
    linux_only = [node for node, why in skipped if any(w in why for w in LINUX_ONLY_SKIP_WORDS)]
    if platform.startswith("linux") and linux_only:
        lines.append(gh_command("warning", "Linux-only tests were skipped on Linux: " + ", ".join(linux_only),
                                title="Linux-only tests did not run"))
    return lines


def annotations_enabled(env) -> bool:
    """On in GitHub Actions; NPD_TEST_ANNOTATIONS=1 shows the same lines in a local run (to preview them)."""
    return env.get("GITHUB_ACTIONS") == "true" or env.get("NPD_TEST_ANNOTATIONS") == "1"


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    if not annotations_enabled(os.environ):                   # 보통의 로컬 실행에서는 아무것도 덧붙이지 않는다
        return
    python = ".".join(str(x) for x in sys.version_info[:3])
    for line in ci_annotations(terminalreporter.stats, sys.platform, python):
        terminalreporter.write_line(line)


__all__ = ["apply_mod", "make_demo", "scan_mod", "show_mod"]
