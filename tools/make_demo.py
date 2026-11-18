#!/usr/bin/env python3
"""Build sample trees, git repositories and zip archives with NFD names - for the README demo, tests and eval fixtures.

Every NFD name is produced at run time with unicodedata.normalize, never typed into a source file (editors and git
may silently turn typed NFD text into NFC).

    python3 tools/make_demo.py demo-tree        # one folder that shows every kind of finding
"""
from __future__ import annotations

import argparse
import hashlib
import os
import struct
import subprocess
import sys
import unicodedata
import zipfile
from pathlib import Path

NAME = "Demo"
GIT_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": NAME, "GIT_AUTHOR_EMAIL": "demo@example.invalid",
    "GIT_COMMITTER_NAME": NAME, "GIT_COMMITTER_EMAIL": "demo@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00+0000", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00+0000",
}
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
GIT_CONFIG = "[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tbare = false\n\tlogallrefupdates = true\n\tprecomposeunicode = true\n"


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def write(path: Path, data: str | bytes = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)


# ---------------------------------------------------------------------------
# plain trees
# ---------------------------------------------------------------------------


def build_site(dest: Path) -> Path:
    """NFD folder and files, an NFC file, and a README whose line 3 links to the NFD logo with an NFC path."""
    write(dest / "README.md", "# Sample site\n\n![logo](" + nfc("이미지/로고.png") + ")\n\nPlain ASCII text follows.\n")
    write(dest / nfd("이미지") / nfd("로고.png"), b"\x89PNG demo")
    write(dest / nfd("ガイド.md"), "# guide\n")
    write(dest / "notes" / nfc("café.md"), "# notes\n")
    return dest


# ---------------------------------------------------------------------------
# git repositories
# ---------------------------------------------------------------------------


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.hooksPath=" + os.devnull, *args], input=data,
                       capture_output=True, env={**os.environ, **GIT_ENV})
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.decode('utf-8', 'replace')}")
    return r.stdout


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-q", "-b", "main", "--template=")
    (repo / ".git" / "config").write_text(GIT_CONFIG)


def stage_paths(repo: Path, entries: list[tuple[str, bytes]]) -> None:
    """Put paths into the index exactly as given (bytes from the str, no normalization) via update-index --index-info."""
    payload = b""
    for path, content in entries:
        blob = git(repo, "hash-object", "-w", "--stdin", data=content).decode().strip()
        payload += f"100644 {blob}\t".encode() + path.encode("utf-8") + b"\0"
    git(repo, "update-index", "-z", "--index-info", data=payload)


def commit(repo: Path, message: str = "chore: add fixture files") -> None:
    git(repo, "commit", "-q", "-m", message)


def build_git_ghost(dest: Path) -> Path:
    """A file committed under its NFD path; the work tree holds the same NFD name (a ghost for precompose git)."""
    init_repo(dest)
    name = nfd("자료.txt")
    write(dest / name, "data\n")
    stage_paths(dest, [(name, b"data\n")])
    commit(dest)
    return dest


def build_index_collision(dest: Path) -> Path:
    """The same path in the index twice: once NFC, once NFD. The work tree holds the NFC file."""
    init_repo(dest)
    write(dest / nfc("café.txt"), "one\n")
    stage_paths(dest, [(nfc("café.txt"), b"one\n"), (nfd("café.txt"), b"two\n")])
    commit(dest)
    return dest


def normalize_index(repo: Path) -> None:
    """Zero the stat fields of .git/index and fix its checksum so the same input gives the same bytes."""
    path = repo / ".git" / "index"
    data = bytearray(path.read_bytes())
    count = struct.unpack(">I", data[8:12])[0]
    pos = 12
    for _ in range(count):
        data[pos:pos + 24] = b"\0" * 24          # ctime, mtime, dev, ino (mode follows and is kept)
        data[pos + 28:pos + 40] = b"\0" * 12     # uid, gid, size
        flags = struct.unpack(">H", data[pos + 60:pos + 62])[0]
        path_len = data.index(b"\0", pos + 62) - (pos + 62) if flags & 0xFFF == 0xFFF else flags & 0xFFF
        # entry length = 62 + path length, padded with 1-8 NUL bytes to a multiple of 8
        pos += (62 + path_len + 8) & ~7
    data[-20:] = hashlib.sha1(bytes(data[:-20])).digest()
    path.write_bytes(bytes(data))


# ---------------------------------------------------------------------------
# zip archives
# ---------------------------------------------------------------------------


def _zinfo(name: str) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, ZIP_DATE)
    zi.compress_type = zipfile.ZIP_STORED
    zi.external_attr = (0o644 << 16) | (0o40000 << 16 if name.endswith("/") else 0)
    return zi


def write_zip(path: Path, entries: list[tuple[str, bytes]]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries:
            zf.writestr(_zinfo(name), data)


def build_names_zip(path: Path) -> Path:
    """NFD folder + NFD file, an ASCII entry, and one legacy entry without the UTF-8 flag (CP949 name)."""
    placeholder = "QQQQQQQQ"
    legacy = "한글.txt".encode("cp949")
    assert len(legacy) == len(placeholder)
    write_zip(path, [(nfd("보고서/"), b""), (nfd("보고서/요약.txt"), b"summary\n"), ("readme.txt", b"hello\n"),
                     (placeholder, b"legacy\n")])
    raw = path.read_bytes()
    assert raw.count(placeholder.encode()) == 2
    path.write_bytes(raw.replace(placeholder.encode(), legacy))
    return path


def build_collision_zip(path: Path) -> Path:
    write_zip(path, [(nfc("café.txt"), b"one\n"), (nfd("café.txt"), b"two\n")])
    return path


def build_truncated_zip(path: Path) -> Path:
    write_zip(path, [("a.txt", b"x" * 400), ("b.txt", b"y" * 400)])
    path.write_bytes(path.read_bytes()[:-30])
    return path


#: .git members a zipped repository keeps: enough for git to open it, read the index and show status.
#: .git/config and .git/hooks/ are left out on purpose: agent sandboxes (Claude Code on macOS, for example) refuse to
#: write .git/config and .git/hooks/* below the working folder, so `python3 -m zipfile -e` would stop at the first such
#: member and leave a repository git cannot open. git opens a repository without .git/config (defaults apply).
GIT_KEEP = (".git/HEAD", ".git/index")
GIT_KEEP_DIRS = (".git/objects/", ".git/refs/")


def pack_dir(src: Path, zip_path: Path, top: str) -> Path:
    """Zip a folder (including the parts of .git that matter) under ``top`` with fixed dates and a stable order."""
    items: list[tuple[str, bytes]] = []
    for p in sorted(src.rglob("*"), key=lambda q: q.relative_to(src).as_posix().encode("utf-8")):
        rel = p.relative_to(src).as_posix()
        if not p.is_file():
            continue
        # .git 은 HEAD·index·objects·refs 만 담는다 (config·hooks 는 샌드박스가 쓰기를 막아 압축 풀기가 중간에 멈춘다)
        if rel.startswith(".git/") and not (rel in GIT_KEEP or rel.startswith(GIT_KEEP_DIRS)):
            continue
        items.append((f"{top}/{rel}", p.read_bytes()))
    write_zip(zip_path, items)
    return zip_path


# ---------------------------------------------------------------------------
# the all-in-one demo folder
# ---------------------------------------------------------------------------


def build_demo(dest: Path) -> Path:
    if dest.exists() and any(dest.iterdir()):
        raise SystemExit(f"error: {dest} already exists and is not empty; pick a new folder")
    build_site(dest)
    name = nfd("자료.txt")
    init_repo(dest)
    write(dest / name, "data\n")
    stage_paths(dest, [(name, b"data\n")])
    commit(dest)
    build_names_zip(dest / "archive.zip")
    return dest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Create a demo folder with NFD names, a ghost git entry and a zip archive.")
    p.add_argument("dest", help="new folder to create (must not exist or be empty)")
    args = p.parse_args(argv)
    dest = build_demo(Path(args.dest))
    print(f"demo folder created: {dest}")
    print(f"try: python3 skills/nfc-path-doctor/scripts/scan.py {dest} --zip {dest}/archive.zip")
    return 0


if __name__ == "__main__":
    sys.exit(main())
