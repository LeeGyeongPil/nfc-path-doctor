# JSON formats: scan output, plan and undo file

All JSON is written with sorted keys and two-space indent, so the same input gives the same bytes. Every path appears as readable text and as UTF-8 hex (`*_hex`), and the hex is the truth. Invalid UTF-8 shows as backslash escapes in the text field only.

## Contents

- Scan output
- Repositories below the scanned folder
- Finding kinds
- Extra scan options
- Plan
- Undo file
- Exit codes

## Scan output

`scan.py --out report.json`:

```json
{
  "version": 1,
  "root": "/abs/path",
  "root_hex": "...",
  "summary": {"nfd_name": 3, "ref_mismatch": 1},
  "findings": [{"kind": "nfd_name", "where": "fs", "path": "...", "path_hex": "...", "nfc": "...", "nfc_hex": "...", "form": "nfd"}],
  "stats": {"entries": 5, "git": "checked", "ref_files": 1, "ref_files_skipped": 0, "skipped_dirs": 0, "zips": []},
  "exit_code": 1
}
```

Findings are sorted by kind, zip, where, repository, path bytes, line, column. `where` is `fs`, `index`, `zip` or `ref`. `form` is `ascii`, `nfc`, `nfd`, `neutral`, `mixed`, `undecodable`, or `collision` for groups.

JSON files written by these scripts (reports, plans, undo files, `show.py --out`) are not searched for references; `stats.ref_files_own_reports` counts them.

`--exit-zero` changes only the process exit status (0 when the scan finished); `exit_code` in the report keeps the result code.

## Repositories below the scanned folder

When ROOT is inside a repository, index paths of that repository are relative to ROOT. Every repository below ROOT (a folder with its own `.git`, for example one just unpacked from a zip) is checked as well, also when ROOT itself lies inside another repository. Its index findings carry `repo` / `repo_hex` (the repository folder relative to ROOT) and their paths are relative to that repository, as `git ls-files` shows them. `stats.repos` lists those folders.

`stats.repos_unreadable` lists folders whose `.git` git cannot open (`.` for ROOT itself), for example after an unpack that stopped early because a sandbox refused to write `.git/config` or `.git/hooks`. Their index was not checked and the table prints a `warning:` line for each: say so instead of reporting a clean result. Neither changes `exit_code`.

`apply.py --apply` replaces index entries in those repositories too. Afterwards `git -C <repo folder> status --porcelain` shows a fixed entry as a staged rename `R  <old> -> <new>`.

## Finding kinds

| Kind | Where | Extra fields | Needs a human |
|---|---|---|---|
| `nfd_name`, `mixed_name` | fs | `entry` (dir, file, link) | no |
| `undecodable_name` | fs, index | | yes |
| `fs_collision` | fs | `members[{path, path_hex, entry}]` | yes |
| `case_collision` (only with `--casefold`) | fs | `members` | no, warning |
| `index_nfd_path` | index | `mode`, `blob`, `on_disk` | no |
| `index_form_mismatch` | index | `mode`, `blob`, `disk`, `disk_hex` | no |
| `index_collision` | index | `members[{path, path_hex, blob, mode}]` | yes |
| `index_conflict_stage` | index | `stages` | yes |
| `zip_nfd_name` | zip | `zip` | no (archive is never changed) |
| `zip_legacy_name` | zip | `zip`, `raw_hex`, `guess{encoding, text}` | no |
| `zip_collision` | zip | `zip`, `members` | yes |
| `ref_mismatch` | ref | `file`, `line`, `col`, `location`, `disk`, `disk_hex` | no |

## Extra scan options

Normal requests need none of these; the command in SKILL.md is enough. `python3 scripts/scan.py --help` lists them all.

| Option | Effect |
|---|---|
| `--no-git` | skip the git index check |
| `--no-refs` | skip the text reference check |
| `--ref-ext EXTS` | comma separated extensions searched for references (default covers Markdown, reST, text, HTML, JSON, YAML, TOML and common source files) |
| `--exclude GLOB` | skip matching entries (repeatable; `.git` is always skipped) |
| `--casefold` | also warn about names that differ only by case after NFC (`case_collision`) |
| `--json` | print the report to stdout instead of the table (`--out` is the better choice) |
| `--max-rows N` | cap the table rows (default 200; the JSON is never cut) |
| `--lang en\|ko` | output language; JSON keys never change |

## Plan

`scan.py --plan-out plan.json`:

```json
{
  "version": 1,
  "target": "NFC",
  "root": "/abs/path",
  "root_hex": "...",
  "git_prefix_hex": "",
  "options": {"no_git": false, "no_refs": false, "ref_ext": [".md"], "exclude": [], "zips": [], "casefold": false},
  "actions": [],
  "blocked": []
}
```

`actions` run in order: reference rewrites, file system renames (deepest first), git index renames.

- `ref_rewrite`: `file`, `file_hex`, `sha256` (content when planned), `occurrences[{line, col, old_hex, new_hex}]`. Only references whose bytes are not NFC are rewritten.
- `fs_rename`: `parent_hex`, `from_hex`, `to_hex` (one name, relative to the root).
- `index_rename`: `from_hex`, `to_hex` (paths from the repository top), `mode`, `blob`, and `repo_hex` for a repository below the root.
- `blocked`: `kind`, `key`, `members`, `reason`. Blocked paths and everything below them are not in `actions`.

Before changing anything `apply.py` checks every step against the current state. If a name, index entry or file hash differs it exits 1 with `plan is stale` and changes nothing.

## Undo file

`apply.py --apply` writes `undo.json` next to the plan (or to `--undo-out`). It has the same shape with `"target": "undo"` and the inverse steps in reverse order. `apply.py --undo undo.json --apply` runs it.

## Exit codes

| Script | Code | Meaning |
|---|---|---|
| scan.py | 0 | clean |
| scan.py | 1 | fixable findings |
| scan.py | 2 | input error (missing or wrong ROOT, broken or truncated zip, git failure); one `error:` line on stderr |
| scan.py | 3 | a human decision is needed |
| scan.py `--exit-zero` | 0 / 2 | scan finished (result code in `exit_code`) / input error |
| show.py | 0 / 2 | ok / input error |
| apply.py | 0 | ok, dry-run included |
| apply.py | 1 | plan is stale, nothing changed |
| apply.py | 2 | input error (unreadable or invalid plan, wrong arguments) |
| apply.py | 4 | a step failed, finished steps were rolled back |
