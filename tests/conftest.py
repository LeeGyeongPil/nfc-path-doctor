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


__all__ = ["apply_mod", "make_demo", "scan_mod", "show_mod"]
