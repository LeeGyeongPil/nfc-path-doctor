#!/usr/bin/env python3
"""Generate the inputs of the task evals. Run from anywhere:  python3 evals/fixtures/build.py

The inputs are zip files and a text file with plain ASCII names, because the eval harness only accepts ASCII fixture
paths. The NFD names live *inside* the zips and are created with unicodedata.normalize here, never typed in a source
file. The task prompts tell the agent to unpack the zip first (python3 -m zipfile -e).
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent.parent / "tools"))

import make_demo as md  # noqa: E402


def build_all(dest: Path) -> list[Path]:
    """Write every fixture below ``dest`` (one folder per task case) and return the files created."""
    out: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="npd-fixtures-") as tmp:
        work = Path(tmp)

        d = dest / "task-site-links"
        d.mkdir(parents=True, exist_ok=True)
        out.append(md.pack_dir(md.build_site(work / "site"), d / "site.zip", "site"))

        # (v2) the git "ghost" fix is no longer an eval case; tests/test_apply.py covers it with unit tests
        d = dest / "task-git-duplicates"
        d.mkdir(parents=True, exist_ok=True)
        repo = md.build_index_collision(work / "dup")
        md.normalize_index(repo)
        out.append(md.pack_dir(repo, d / "dup.zip", "dup"))

        d = dest / "task-zip-precheck"
        d.mkdir(parents=True, exist_ok=True)
        out.append(md.build_names_zip(d / "archive.zip"))

        d = dest / "task-names-compare"
        d.mkdir(parents=True, exist_ok=True)
        (d / "names.txt").write_bytes((md.nfc("café") + "\n" + md.nfd("café") + "\n").encode("utf-8"))
        out.append(d / "names.txt")

        d = dest / "task-error-truncated-zip"
        d.mkdir(parents=True, exist_ok=True)
        out.append(md.build_truncated_zip(d / "backup.zip"))
    return out


def main() -> int:
    files = build_all(HERE)
    for f in files:
        print(f"wrote {f.relative_to(HERE.parent.parent)} ({f.stat().st_size} bytes)")
    shutil.rmtree(HERE / "__pycache__", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
