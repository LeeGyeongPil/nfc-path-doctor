# Changelog

All notable changes to this project are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-19

### Added

- `show.py`: form (ascii, nfc, nfd, neutral, mixed, undecodable), code points and UTF-8 bytes of names, byte and NFC comparison of several names, `--path` to read the bytes stored on disk.
- `scan.py`: compares the work tree, the git index, zip archives and text references; JSON report, table output, optional fix plan; exit codes 0, 1, 2, 3 (`--exit-zero` exits 0 when the scan finished and keeps the result code in the report). Git repositories below the scanned folder (for example one just unpacked from a zip) are checked too, also when the scanned folder itself lies inside another repository; a `.git` that git cannot open is reported (`stats.repos_unreadable` and a warning line) instead of a silent clean result. The scripts' own JSON outputs are not counted as references.
- `apply.py`: dry-run by default, `--apply` renames deepest names first, replaces git index entries without changing git config, rewrites references, verifies the plan first, rolls back on failure, writes an undo file; `--undo`.
- `scan.py`, `show.py` and `apply.py` refuse to overwrite an existing `--out`, `--plan-out` or undo file that these scripts did not write (exit 2, with a way forward); `apply.py` rejects plan paths that are absolute or contain `..`.
- English and Korean output (`--lang`, `NFC_PATH_DOCTOR_LANG`, locale).
- Agent skill `nfc-path-doctor` with Claude Code plugin manifest, evaluation set and tests. The skill has one entry point: every request starts with the same `scan.py` command; `show.py` only compares names the user gives and `apply.py` only runs when the user asks for a fix. Secondary scan options are documented in `references/formats.md` and `--help`.

[0.1.0]: https://github.com/LeeGyeongPil/nfc-path-doctor/releases/tag/v0.1.0
