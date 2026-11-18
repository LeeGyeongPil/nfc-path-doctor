# Background: why the same-looking name differs

Sources were read on 2026-11-18. Behavior notes marked "measured" were observed on macOS (APFS) with Python 3.15 and git 2.46.

## Contents

- Normalization forms
- File systems
- git
- zip archives
- Text references
- Sources

## Normalization forms

- NFC composes characters: a Hangul syllable is one code point (U+AC00 to U+D7A3), `é` is U+00E9.
- NFD decomposes them: a syllable becomes two or three jamo (leading U+1100.., vowel U+1161.., trailing U+11A8..), `é` becomes `e` U+0065 plus U+0301, Japanese `が` becomes `か` U+304B plus U+3099.
- Measured: the four-syllable word 한글문서 is 4 code points and 12 UTF-8 bytes in NFC, 11 code points and 33 bytes in NFD.
- NFKC and NFKD also fold compatibility characters (for example the ligature ﬁ becomes fi). They turn a name into a different name, so they are never used for paths.

## File systems

| File system | Stores | Compares | Consequence |
|---|---|---|---|
| APFS (macOS default) | the bytes you give (normalization preserving) | ignoring normalization | NFC and NFD of one name are the same file; both forms cannot coexist in one folder |
| HFS+ (older macOS) | NFD forced | NFD | a rename to NFC is silently stored as NFD again |
| ext4, XFS (Linux) | raw bytes | raw bytes | NFC and NFD are two different files and can coexist |
| NTFS (Windows) | raw UTF-16 | raw | like Linux for this purpose; not verified by this skill |

Measured on APFS: a file created with an NFD name is listed back as NFD, opening it with the NFC name works, creating the NFC name fails with "file exists", and a rename from NFD to NFC really changes the stored form.

Why it matters: macOS opens a link written in NFC even when the file is stored as NFD, so everything seems fine until the same tree is checked out on Linux or rendered by GitHub.

## git

- The index and trees hold raw byte paths.
- On macOS `core.precomposeunicode` (written as `true` into the repository config by `git init`; a repository copied without `.git/config` does not have it) makes git turn the decomposed names that the file system returns into composed names. A path committed earlier in NFD, or added by another client, then shows as an untracked NFC file while the NFD entry stays in the index.
- `git status` quotes non-ASCII bytes as octal escapes unless told otherwise, so tools read paths with `-z`.
- `git mv` also composes its arguments, so it cannot express an NFD to NFC rename. Feeding `git update-index --index-info` on stdin does not compose, which is what `apply.py` uses: one line deleting the old path (mode 0, all-zero object name) and one line adding the same blob and mode under the new path.

## zip archives

- Each entry has a general purpose flag, bit 11 (0x800). When set the name is UTF-8, otherwise it is read in a legacy code page (CP437 by default, CP949 or Shift-JIS depending on the tool).
- The normalization form of a name is independent of that flag. An archive made on macOS usually keeps the NFD names.
- Python writes the UTF-8 flag itself for non-ASCII names. Reading an entry without the flag gives mojibake unless the right legacy encoding is chosen; `scan.py` reports the raw bytes and a guess.

## Text references

Links in Markdown, paths in code and configuration are plain text. The scan resolves a path-like token against the real tree by comparing NFC keys, then compares the token's bytes with the real names. A token whose bytes differ is a `ref_mismatch`. The scan does not decode percent-encoded URLs and only looks at files with the extensions listed by `--ref-ext`.

## Sources

- https://developer.apple.com/library/archive/documentation/FileManagement/Conceptual/APFS_Guide/FAQ/FAQ.html (APFS normalization, archived guide), read 2026-11-18
- https://raw.githubusercontent.com/git/git/master/Documentation/config/core.adoc (`core.precomposeUnicode`), read 2026-11-18
- https://docs.python.org/3/library/zipfile.html (name encoding flag, `metadata_encoding`), read 2026-11-18
- https://docs.python.org/3/library/unicodedata.html (`normalize`, `is_normalized`), read 2026-11-18
- https://docs.syntevo.com/SmartGit/Latest/HowTos/TroubleShooting/Deduplicate-Unicode-Files-On-MacOS (duplicate NFC/NFD paths in the index), read 2026-11-18
