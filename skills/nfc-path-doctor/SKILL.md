---
name: nfc-path-doctor
description: Diagnoses and safely fixes Unicode normalization (NFC vs NFD) mismatches in file and folder names by comparing the real bytes on disk, in the git index, in zip archives and in text references, then builds a dry-run rename plan with an undo file. Use when a file that clearly exists is reported missing (for example a Korean file name made on a Mac is "not found" on a Linux server although it looks identical), glob or grep misses Korean, Japanese or accented names, git shows the same non-ASCII file twice or as untracked after a commit, a zip made on macOS has broken or jamo-split names on Windows or Linux, links work on macOS but break on Linux or GitHub, two names look the same but compare as different (byte for byte), or a folder or git repository copied or unzipped from a Mac needs checking. Not for text encoding conversion, normalizing file contents, or case-only renames.
license: MIT
compatibility: Python 3.10+ (standard library only); git is optional and only needed for the index check. No network access.
---

# NFC path doctor

NFC and NFD names look identical on screen, so neither you nor the user can tell them apart by eye. The scripts read the real bytes: answer only from their output, never from `ls`, `git ls-files`, your own Python checks or a report you write yourself. A diagnosis is one scan and one answer: do not look around before or after the scan.

The scripts are `scripts/scan.py` (the entry point for every request), `scripts/show.py` (only to compare names the user gives) and `scripts/apply.py` (only when the user asks for a fix). `<skill-dir>` below is this skill's base directory (the folder of this SKILL.md, shown when the skill loads); always use its absolute path.

## Step 1: always scan first

Stay in the user's folder (do not `cd` into the skill) and run this exact command with Bash:

```
python3 <skill-dir>/scripts/scan.py . --exit-zero --out report.json --plan-out plan.json
```

- For a zip file add `--zip FILE.zip` and do not unpack it (the zip is never modified): `python3 <skill-dir>/scripts/scan.py . --exit-zero --zip FILE.zip --out report.json --plan-out plan.json`
- If the user told you to unpack something, unpack it first, then run the scan on `.` (git repositories inside are checked too).
- If the user names the output file, pass that name to `--out`. The script writes the whole report; never write or edit it yourself.
- Run the command as written: no wrapper shell file, no shell variable for the path, no `| head`. An `error:` line (exit 2) is the answer; show it as is.

## Step 2: answer at once from the printed output

- The scan prints what it checked, every finding with its bytes and kind, and ends with a `Done:` line. That output is complete: do not open `report.json`, `plan.json` or the references and do not re-check with `ls`, `git` or `cat`; reply now. Only if it says `more finding(s) not shown` (over 200 findings), read `findings` in `report.json` once for the rest, then reply.
- `Nothing here to examine`: no non-ASCII name in this folder, so the user's files are elsewhere. Explain the printed cause and ask for the folder or zip; never search the disk.
- `summary` counts each kind: `nfd_name`, `mixed_name` decomposed names ("not found" on Linux), `index_*` a non-NFC git path, `zip_*` archive names (`raw_hex`, `guess` for legacy names), `ref_mismatch` a link that breaks on Linux or GitHub (give its `location`, file:line:column).
- `*_collision`, `index_conflict_stage`, `undecodable_name`: a human decides. Report the `members`, change nothing, ask.
- Quote `path_hex` / `nfc_hex` (the `bytes:` lines); never retype a suspicious name.

## Only if the user gave names to compare

Run `python3 <skill-dir>/scripts/show.py --file names.txt --out names.json` (or pass the names as arguments) and answer from `same_bytes`, `same_nfc` and `codepoints`.

## Only if the user asked for a fix

1. `python3 <skill-dir>/scripts/apply.py plan.json` lists the steps and changes nothing.
2. `python3 <skill-dir>/scripts/apply.py plan.json --apply` renames, replaces git index entries, rewrites links, writes `undo.json` and re-scans. Then show `git -C <repo folder> status --porcelain`.
   If `undo.json` still holds an unreverted earlier undo record, `--apply` exits 2 before changing anything; use `--undo-out other.json` or undo first.
3. Undo: `python3 <skill-dir>/scripts/apply.py --undo undo.json --apply`.

## Rules

- Never change git config and never rename by hand (`mv`, `git mv`, `git rm --cached`): git may fold both forms into one.
- The target form is always NFC (never NFKC/NFKD). Zip files are only inspected.
- `test -e` being true on macOS proves nothing: APFS ignores normalization.

More, only for questions the scan output leaves open: `references/formats.md` (JSON fields, exit codes, nested repositories, warnings, extra options), `references/background.md` (NFC/NFD, filesystems, git, zip).
