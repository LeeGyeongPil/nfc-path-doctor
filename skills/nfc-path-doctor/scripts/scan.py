#!/usr/bin/env python3
"""Scan a folder tree, its git index, zip archives and text references for NFC/NFD name mismatches.

Exit codes: 0 clean, 1 fixable findings, 2 input error, 3 findings that need a human decision.
"""
from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import sys
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _npd as n  # noqa: E402

DEFAULT_REF_EXT = ".md,.mdx,.rst,.txt,.html,.json,.yaml,.yml,.toml,.py,.js,.ts,.tsx,.jsx,.css"
MAX_REF_BYTES = 2 * 1024 * 1024
# a path-like run of characters: stops at whitespace, quotes, brackets and URL/markup delimiters
TOKEN_RE = re.compile(r"[^\s\"'`()<>\[\]{}|,;*?#=]+")

HUMAN_KINDS = {"fs_collision", "index_collision", "index_conflict_stage", "zip_collision", "undecodable_name"}
WARN_KINDS = {"case_collision"}
ALL_KINDS = ["nfd_name", "mixed_name", "undecodable_name", "fs_collision", "index_nfd_path", "index_form_mismatch",
             "index_collision", "index_conflict_stage", "zip_nfd_name", "zip_legacy_name", "zip_collision",
             "ref_mismatch", "case_collision"]


# ---------------------------------------------------------------------------
# file system walk
# ---------------------------------------------------------------------------


@dataclass
class Entry:
    rel: str      # path below the root, '/' separated, exactly as stored on disk
    name: str
    parent: str   # '' for the root
    kind: str     # dir | file | link | other


@dataclass
class Tree:
    entries: list[Entry] = field(default_factory=list)
    kids: dict[str, dict[str, list[str]]] = field(default_factory=dict)   # parent -> nfc(name) -> actual names
    kind_of: dict[str, str] = field(default_factory=dict)                 # rel -> kind
    by_nfc: dict[str, list[str]] = field(default_factory=dict)            # nfc(rel) -> actual rels
    repos: list[str] = field(default_factory=list)                        # folders that hold a .git entry
    skipped: int = 0

    def add(self, e: Entry) -> None:
        self.entries.append(e)
        self.kids.setdefault(e.parent, {}).setdefault(n.nfc(e.name), []).append(e.name)
        self.kind_of[e.rel] = e.kind
        self.by_nfc.setdefault(n.nfc(e.rel), []).append(e.rel)


def is_excluded(name: str, rel: str, patterns: list[str]) -> bool:
    for pat in patterns:
        p = n.nfc(pat)
        if fnmatch.fnmatchcase(n.nfc(name), p) or fnmatch.fnmatchcase(n.nfc(rel), p):
            return True
    return False


def walk_tree(root: str, excludes: list[str]) -> Tree:
    """Walk with byte names so that NFD and NFC names come back exactly as stored. Symlinks are not followed."""
    tree = Tree()
    stack: list[tuple[bytes, str]] = [(os.fsencode(root), "")]
    while stack:
        dir_b, parent = stack.pop()
        try:
            with os.scandir(dir_b) as it:
                items = sorted(it, key=lambda e: e.name)
        except OSError:
            tree.skipped += 1
            continue
        for e in items:
            name = n.decode_name(e.name)
            if name == ".git":
                # 이 폴더는 git 저장소다: 색인은 run_scan 이 따로 읽는다 (압축을 푼 저장소 같은 하위 저장소 포함)
                tree.repos.append(parent)
                continue
            rel = n.join_rel(parent, name)
            if is_excluded(name, rel, excludes):
                continue
            try:
                link = e.is_symlink()
                is_dir = (not link) and e.is_dir(follow_symlinks=False)
                is_file = (not link) and e.is_file(follow_symlinks=False)
            except OSError:
                link = is_dir = is_file = False
            kind = "link" if link else "dir" if is_dir else "file" if is_file else "other"
            tree.add(Entry(rel, name, parent, kind))
            if kind == "dir":
                stack.append((os.path.join(dir_b, e.name), rel))
    tree.entries.sort(key=lambda x: n.encode_name(x.rel))
    return tree


def mk(kind: str, where: str, path: str, **extra: object) -> dict:
    d = {"kind": kind, "where": where, "path": n.disp(path), "path_hex": n.hexs(path), "nfc": n.disp(n.nfc(path)),
         "nfc_hex": n.hexs(n.nfc(path)), "form": n.classify(path)}
    d.update(extra)
    return d


def member(path: str, **extra: object) -> dict:
    d = {"path": n.disp(path), "path_hex": n.hexs(path)}
    d.update(extra)
    return d


def fs_findings(tree: Tree, casefold: bool) -> tuple[list[dict], list[dict]]:
    """Return (findings, blocked) for the work tree."""
    out: list[dict] = []
    blocked: list[dict] = []
    kinds = {"nfd": "nfd_name", "mixed": "mixed_name", "undecodable": "undecodable_name"}
    for e in tree.entries:
        f = n.classify(e.name)
        if f in kinds:
            out.append(mk(kinds[f], "fs", e.rel, entry=e.kind))
            if f == "undecodable":
                blocked.append({"kind": "undecodable_name", "key": n.disp(e.rel), "key_hex": n.hexs(e.rel),
                                "members": [member(e.rel)], "reason": "name is not valid UTF-8"})
    for parent, groups in tree.kids.items():
        for key, names in groups.items():
            if len(set(names)) > 1:
                rels = sorted((n.join_rel(parent, x) for x in set(names)), key=n.encode_name)
                keyp = n.join_rel(parent, key)
                mem = [member(r, entry=tree.kind_of[r]) for r in rels]
                out.append(mk("fs_collision", "fs", keyp, form="collision", members=mem))
                blocked.append({"kind": "fs_collision", "key": n.disp(keyp), "key_hex": n.hexs(keyp), "members": mem,
                                "reason": "several entries in one folder fold to the same NFC name"})
    if casefold:
        folded: dict[tuple[str, str], set[str]] = defaultdict(set)
        for e in tree.entries:
            folded[(e.parent, n.nfc(e.name).casefold())].add(n.nfc(e.name))
        for (parent, key), names in sorted(folded.items(), key=lambda kv: n.encode_name(kv[0][0] + "/" + kv[0][1])):
            if len(names) > 1:
                keyp = n.join_rel(parent, key)
                out.append(mk("case_collision", "fs", keyp, form="collision",
                              members=[member(n.join_rel(parent, x)) for x in sorted(names, key=n.encode_name)]))
    return out, blocked


# ---------------------------------------------------------------------------
# git index
# ---------------------------------------------------------------------------


def index_findings(entries: list[tuple[str, str, int, bytes]], prefix: bytes, tree: Tree, base: str = ""
                   ) -> tuple[list[dict], list[dict], list[dict]]:
    """Return (findings, blocked, index_actions) for stage-0 entries and conflict stages.

    ``base`` is the repository folder below the scan root for a nested repository ('' otherwise). Index paths stay
    relative to the repository, as git shows them; only the lookup of the same file on disk adds ``base``.
    """
    out: list[dict] = []
    blocked: list[dict] = []
    actions: list[dict] = []
    stages: dict[str, list[int]] = defaultdict(list)
    stage0: list[tuple[str, str, str, bytes]] = []
    for mode, blob, stage, full in entries:
        rel_b = full[len(prefix):] if full.startswith(prefix) else full
        rel = n.decode_name(rel_b)
        if stage:
            stages[rel].append(stage)
        else:
            stage0.append((mode, blob, rel, full))
    for rel, st in sorted(stages.items(), key=lambda kv: n.encode_name(kv[0])):
        out.append(mk("index_conflict_stage", "index", rel, stages=sorted(st)))
        blocked.append({"kind": "index_conflict_stage", "key": n.disp(rel), "key_hex": n.hexs(rel),
                        "members": [member(rel, stages=sorted(st))], "reason": "entry is in a merge-conflict stage"})
    groups: dict[str, list[tuple[str, str, str, bytes]]] = defaultdict(list)
    for item in stage0:
        groups[n.nfc(item[2])].append(item)
    in_collision: set[str] = set()
    for key, items in sorted(groups.items(), key=lambda kv: n.encode_name(kv[0])):
        if len({i[2] for i in items}) > 1:
            mem = [member(i[2], blob=i[1], mode=i[0]) for i in sorted(items, key=lambda i: n.encode_name(i[2]))]
            out.append(mk("index_collision", "index", key, form="collision", members=mem))
            blocked.append({"kind": "index_collision", "key": n.disp(key), "key_hex": n.hexs(key), "members": mem,
                            "reason": "the index holds this path in several forms"})
            in_collision.update(i[2] for i in items)
    for mode, blob, rel, full in sorted(stage0, key=lambda i: n.encode_name(i[2])):
        if rel in in_collision:
            continue
        form = n.classify(rel)
        if form == "undecodable":
            out.append(mk("undecodable_name", "index", rel, mode=mode, blob=blob))
            continue
        # 같은 파일을 디스크에서 NFC 키로 찾는다 (하위 저장소면 저장소 폴더를 앞에 붙여 찾고, 결과에서는 다시 뗀다)
        disk = [d[len(base) + 1:] if base else d for d in tree.by_nfc.get(n.nfc(n.join_rel(base, rel)), [])]
        if disk and rel not in disk:
            out.append(mk("index_form_mismatch", "index", rel, mode=mode, blob=blob, disk=n.disp(disk[0]),
                          disk_hex=n.hexs(disk[0])))
        elif rel != n.nfc(rel):
            out.append(mk("index_nfd_path", "index", rel, mode=mode, blob=blob, on_disk=bool(disk)))
        if rel != n.nfc(rel):
            new_full = n.encode_name(n.nfc(n.decode_name(full)))
            actions.append({"op": "index_rename", "path": n.disp(rel), "from_hex": full.hex(), "to_hex": new_full.hex(),
                            "mode": mode, "blob": blob})
    return out, blocked, actions


# ---------------------------------------------------------------------------
# zip archives
# ---------------------------------------------------------------------------


def zip_findings(zpath: str) -> list[dict]:
    try:
        zf = zipfile.ZipFile(zpath)
    except FileNotFoundError as e:
        raise n.ToolError("err_path_not_found", path=n.disp(zpath)) from e
    except (zipfile.BadZipFile, OSError, UnicodeDecodeError, NotImplementedError, EOFError) as e:
        raise n.ToolError("err_zip_invalid", path=n.disp(zpath)) from e
    out: list[dict] = []
    names: dict[str, set[str]] = defaultdict(set)
    with zf:
        for info in zf.infolist():
            if info.flag_bits & 0x800 or info.orig_filename.isascii():
                name = info.filename
                form = n.classify(name)
                if form in ("nfd", "mixed"):
                    out.append(mk("zip_nfd_name", "zip", name, zip=n.disp(zpath)))
                if not name.isascii():
                    names[n.nfc(name)].add(name)
                continue
            raw = info.orig_filename.encode("cp437")
            guess: dict = {"encoding": None, "text": None}
            for enc in ("utf-8", "cp949"):
                try:
                    guess = {"encoding": enc, "text": raw.decode(enc)}
                    break
                except UnicodeDecodeError:
                    continue
            out.append(mk("zip_legacy_name", "zip", n.decode_name(raw), zip=n.disp(zpath), raw_hex=raw.hex(), guess=guess))
    for key, group in sorted(names.items(), key=lambda kv: n.encode_name(kv[0])):
        if len(group) > 1:
            out.append(mk("zip_collision", "zip", key, form="collision", zip=n.disp(zpath),
                          members=[member(x) for x in sorted(group, key=n.encode_name)]))
    return out


# ---------------------------------------------------------------------------
# text references
# ---------------------------------------------------------------------------


def resolve_ref(tree: Tree, base: str, token: str) -> tuple[str, list[str]] | None:
    """Resolve a path-like token against the real folder tree.

    Returns (expected text with the real on-disk names, resolved entry paths) or None when the token is not a path to
    something that exists (compared by NFC key, so both forms of the name find the file).
    """
    if token.startswith("/"):
        return None
    comps = token.split("/")
    cur = base
    out: list[str] = []
    seen: list[str] = []
    for i, c in enumerate(comps):
        last = i == len(comps) - 1
        if c in ("", "."):
            if c == "" and not last:
                return None
            out.append(c)
            continue
        if c == "..":
            if not cur:
                return None
            cur = cur.rsplit("/", 1)[0] if "/" in cur else ""
            out.append(c)
            continue
        names = tree.kids.get(cur, {}).get(n.nfc(c))
        if not names or len(set(names)) != 1:
            return None
        actual = names[0]
        cur = n.join_rel(cur, actual)
        if not last and tree.kind_of.get(cur) != "dir":
            return None
        out.append(actual)
        seen.append(cur)
    if not seen:
        return None
    return "/".join(out), seen


def is_own_output(name: str, text: str) -> bool:
    """True for a JSON report, plan or undo file written by scan.py / apply.py, or a show.py --out file."""
    return n.is_own_output(name, text)


def scan_refs(root: str, tree: Tree, exts: set[str]) -> tuple[list[dict], dict[str, dict], dict]:
    """Return (ref hits, per-file info, stats). A hit is one path-like token that resolves to a real entry."""
    hits: list[dict] = []
    files: dict[str, dict] = {}
    stats = {"files": 0, "skipped": 0}
    root_b = os.fsencode(root)
    for e in tree.entries:
        if e.kind != "file" or os.path.splitext(e.name)[1].lower() not in exts:
            continue
        path_b = os.path.join(root_b, n.encode_name(e.rel))
        try:
            if os.path.getsize(path_b) > MAX_REF_BYTES:
                stats["skipped"] += 1
                continue
            with open(path_b, "rb") as fh:
                data = fh.read()
            text = data.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            stats["skipped"] += 1
            continue
        if "\0" in text:
            stats["skipped"] += 1
            continue
        if is_own_output(e.name, text):
            # 이 도구가 쓴 보고서·계획·undo 파일은 이름을 바이트 그대로 담고 있어 참조로 세면 안 된다
            stats["own_outputs"] = stats.get("own_outputs", 0) + 1
            continue
        stats["files"] += 1
        files[e.rel] = {"sha256": hashlib.sha256(data).hexdigest()}
        for lineno, line in enumerate(text.split("\n"), 1):
            if line.isascii():
                continue
            for m in TOKEN_RE.finditer(line):
                tok = m.group().rstrip(".:!")
                if tok.isascii() or ("/" not in tok and "." not in tok):
                    continue
                res = resolve_ref(tree, e.parent, tok) or (resolve_ref(tree, "", tok) if e.parent else None)
                if res is None:
                    continue
                expected, seen = res
                hits.append({"file": e.rel, "line": lineno, "col": m.start() + 1, "token": tok, "expected": expected,
                             "seen": seen})
    return hits, files, stats


def ref_findings(hits: list[dict]) -> list[dict]:
    out = []
    for h in hits:
        if h["token"] != h["expected"]:
            out.append(mk("ref_mismatch", "ref", h["token"], file=n.disp(h["file"]), line=h["line"], col=h["col"],
                          location=f"{n.disp(h['file'])}:{h['line']}:{h['col']}", disk=n.disp(h["expected"]),
                          disk_hex=n.hexs(h["expected"])))
    return out


# ---------------------------------------------------------------------------
# putting it together
# ---------------------------------------------------------------------------


def sort_key(f: dict) -> tuple:
    return (f["kind"], f.get("zip", ""), f["where"], f.get("repo_hex", ""), n.encode_name(f["path"]), f.get("line", 0),
            f.get("col", 0))


def exit_code(findings: list[dict]) -> int:
    kinds = {f["kind"] for f in findings} - WARN_KINDS
    if kinds & HUMAN_KINDS:
        return 3
    return 1 if kinds else 0


def is_blocked(rel: str, blocked_paths: set[str]) -> bool:
    cur = ""
    for part in rel.split("/"):
        cur = n.join_rel(cur, part)
        if cur in blocked_paths:
            return True
    return False


def build_plan(root: str, opts: dict, tree: Tree, fs_blocked: list[dict], index_blocked: list[dict],
               index_actions: list[dict], prefix: bytes | None, hits: list[dict], files: dict[str, dict]) -> dict:
    blocked_paths = {n.decode_name(bytes.fromhex(m["path_hex"])) for b in fs_blocked for m in b["members"]}
    fs_actions = []
    for e in tree.entries:
        if n.classify(e.name) in ("nfd", "mixed") and not is_blocked(e.rel, blocked_paths):
            depth = e.rel.count("/")
            fs_actions.append((depth, n.encode_name(e.rel), {
                "op": "fs_rename", "path": n.disp(e.rel), "parent_hex": n.hexs(e.parent), "from_hex": n.hexs(e.name),
                "to_hex": n.hexs(n.nfc(e.name))}))
    fs_actions.sort(key=lambda t: (-t[0], t[1]))
    per_file: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        if h["token"] == n.nfc(h["token"]) or any(is_blocked(s, blocked_paths) for s in h["seen"]):
            continue
        per_file[h["file"]].append({"line": h["line"], "col": h["col"], "old_hex": n.hexs(h["token"]),
                                    "new_hex": n.hexs(n.nfc(h["token"]))})
    ref_actions = [{"op": "ref_rewrite", "file": n.disp(f), "file_hex": n.hexs(f), "sha256": files[f]["sha256"],
                    "occurrences": sorted(occ, key=lambda o: (o["line"], o["col"]))}
                   for f, occ in sorted(per_file.items(), key=lambda kv: n.encode_name(kv[0]))]
    return {
        "version": n.REPORT_VERSION,
        "target": "NFC",
        "root": n.disp(root),
        "root_hex": n.hexs(root),
        "git_prefix_hex": prefix.hex() if prefix is not None else None,
        "options": opts,
        "actions": ref_actions + [a[2] for a in fs_actions] + index_actions,
        "blocked": sorted(fs_blocked + index_blocked, key=lambda b: (b["kind"], b.get("repo_hex", ""), b["key_hex"])),
    }


def nested_repos(root: str, tree: Tree, root_prefix: bytes | None) -> tuple[list[str], list[str]]:
    """Sort the folders that hold a ``.git`` entry into (repositories below root, folders git cannot open).

    A folder below root is a repository of its own when git says it is the top of a work tree. That holds also when
    root itself sits inside another repository (a zip unpacked inside a project folder): git stops at the nearest
    ``.git``. A ``.git`` that git cannot open (an unpack that stopped early, a copy without objects) makes git fall
    through to the outer repository or fail; such folders are returned in the second list so the scan can say that
    their index was not checked instead of reporting a clean tree. Both lists are in byte order.
    """
    repos: list[str] = []
    unreadable: list[str] = []
    if not tree.repos or not n.git_available():
        return repos, unreadable
    for rel in sorted(set(tree.repos), key=n.encode_name):
        if not rel:
            # ROOT 자체의 .git: ROOT 가 저장소 맨 위로 열렸으면 위에서 이미 검사했다
            if root_prefix != b"":
                unreadable.append(".")
            continue
        if n.git_prefix(os.path.join(root, rel)) == b"":
            repos.append(rel)
        else:
            unreadable.append(rel)
    return repos, unreadable


def run_scan(root: str, zips: list[str], no_git: bool, no_refs: bool, ref_ext: str, excludes: list[str],
             casefold: bool) -> tuple[dict, dict]:
    """Run every check. Returns (report, plan). Raises ToolError on input errors."""
    if not os.path.lexists(os.fsencode(root)):
        raise n.ToolError("err_path_not_found", path=n.disp(root))
    if not os.path.isdir(os.fsencode(root)):
        raise n.ToolError("err_not_dir", path=n.disp(root))
    root = os.path.abspath(root)
    exts = {("." + x.strip().lstrip(".")).lower() for x in ref_ext.split(",") if x.strip()}
    zip_abs = [os.path.abspath(z) for z in zips]
    opts = {"no_git": no_git, "no_refs": no_refs, "ref_ext": sorted(exts), "exclude": list(excludes),
            "zips": zip_abs, "casefold": casefold}
    tree = walk_tree(root, excludes)
    findings, fs_blocked = fs_findings(tree, casefold)
    stats: dict = {"entries": len(tree.entries), "skipped_dirs": tree.skipped, "git": "skipped (--no-git)" if no_git else "no repository"}
    # 비ASCII 이름이 하나도 없으면 정규화 문제가 생길 수 없다: 표가 "여기에는 검사할 것이 없음"을 알려 줄 근거
    stats["entries_non_ascii"] = sum(1 for e in tree.entries if not n.encode_name(e.name).isascii())
    index_blocked: list[dict] = []
    index_actions: list[dict] = []
    prefix: bytes | None = None
    if not no_git:
        prefix = n.git_prefix(root)
        if prefix is not None:
            ents = n.git_index(root)
            f, index_blocked, index_actions = index_findings(ents, prefix, tree)
            findings += f
            stats["git"] = "checked"
            stats["index_entries"] = len(ents)
            stats["index_non_ascii"] = sum(1 for e in ents if not e[3].isascii())
        # ROOT 아래 저장소(예: 방금 압축을 푼 저장소)의 색인도 하나씩 검사한다.
        # ROOT 가 다른 저장소(프로젝트 폴더 등) 안에 있어도 마찬가지다: 바깥 색인에는 안쪽 저장소의 파일이 없다.
        repos, unreadable = nested_repos(root, tree, prefix)
        if unreadable:
            # .git 이 있는데 git 이 열지 못한 폴더: 색인을 못 봤으니 "깨끗함"으로 끝내지 않고 경고로 남긴다
            stats["repos_unreadable"] = [n.disp(r) for r in unreadable]
        if repos:
            stats["git"] = "checked"
            stats.setdefault("index_entries", 0)
            stats.setdefault("index_non_ascii", 0)
            stats["repos"] = [n.disp(r) for r in repos]
        for repo in repos:
            ents = n.git_index(os.path.join(root, repo))
            f, b, a = index_findings(ents, b"", tree, base=repo)
            tag = {"repo": n.disp(repo), "repo_hex": n.hexs(repo)}
            findings += [{**x, **tag} for x in f]
            index_blocked += [{**x, **tag} for x in b]
            index_actions += [{**x, **tag} for x in a]
            stats["index_entries"] += len(ents)
            stats["index_non_ascii"] += sum(1 for e in ents if not e[3].isascii())
    hits: list[dict] = []
    files: dict[str, dict] = {}
    if not no_refs:
        hits, files, rstats = scan_refs(root, tree, exts)
        findings += ref_findings(hits)
        stats["ref_files"] = rstats["files"]
        stats["ref_files_skipped"] = rstats["skipped"]
        if rstats.get("own_outputs"):
            stats["ref_files_own_reports"] = rstats["own_outputs"]
    for z in zip_abs:
        findings += zip_findings(z)
    stats["zips"] = [n.disp(z) for z in zip_abs]
    findings.sort(key=sort_key)
    summary: dict[str, int] = {}
    for f in findings:
        summary[f["kind"]] = summary.get(f["kind"], 0) + 1
    code = exit_code(findings)
    report = {"version": n.REPORT_VERSION, "root": n.disp(root), "root_hex": n.hexs(root), "summary": summary,
              "findings": findings, "stats": stats, "exit_code": code}
    plan = build_plan(root, opts, tree, fs_blocked, index_blocked, index_actions, prefix, hits, files)
    return report, plan


# ---------------------------------------------------------------------------
# table output
# ---------------------------------------------------------------------------


def summary_text(summary: dict[str, int]) -> str:
    return ", ".join(f"{k} {summary[k]}" for k in ALL_KINDS if k in summary)


def checked_line(report: dict, lang: str) -> str:
    """What the scan actually looked at, so that a clean result is never read as 'checked everything'."""
    st = report["stats"]
    if st["git"] == "checked":
        git = n.msg(lang, "scan_git_checked", n=st.get("index_entries", 0), non_ascii=st.get("index_non_ascii", 0))
    elif st["git"].startswith("skipped"):
        git = n.msg(lang, "scan_git_skipped")
    else:
        git = n.msg(lang, "scan_git_none")
    return n.msg(lang, "scan_checked", names=st["entries"], names_non_ascii=st.get("entries_non_ascii", 0), git=git,
                 refs=st.get("ref_files", 0), zips=len(st.get("zips", [])))


def nothing_here(report: dict) -> bool:
    """True when nothing below ROOT can have a normalization problem: no non-ASCII name on disk or in a git index,
    no zip and no repository that git could not open. Never true with --no-git: the index was not read."""
    st = report["stats"]
    if st["git"].startswith("skipped"):
        # 색인을 읽지 않았으니 "색인에도 비ASCII 이름이 없다"고 말할 수 없다
        return False
    return (not report["findings"] and st.get("entries_non_ascii", 0) == 0 and st.get("index_non_ascii", 0) == 0
            and not st.get("zips") and not st.get("repos_unreadable"))


def done_line(report: dict, lang: str, max_rows: int = 200, out_path: str | None = None) -> str:
    """Last line of the table: whether the printed lines are the whole result (so nobody re-checks by hand),
    and where the rest is when the table was cut at --max-rows."""
    total = len(report["findings"])
    if total > max_rows:
        # 표가 잘렸다: "전체 결과"라고 하지 않고 나머지가 있는 곳을 정확히 알린다
        if out_path:
            return n.msg(lang, "scan_done_partial", shown=max_rows, total=total, path=out_path)
        return n.msg(lang, "scan_done_partial_no_out", shown=max_rows, total=total)
    if nothing_here(report):
        return n.msg(lang, "scan_done_elsewhere")
    # 고칠 거리가 있으면(1·3) "다음:" 줄의 apply.py 는 이름을 고칠 때만 쓴다
    return n.msg(lang, "scan_done_fix" if report["exit_code"] in (1, 3) else "scan_done")


def render_table(report: dict, lang: str, max_rows: int, exit_zero: bool = False, plan_path: str | None = None,
                 out_path: str | None = None) -> str:
    m = lambda key, **kw: n.msg(lang, key, **kw)  # noqa: E731
    lines = [m("scan_title", root=report["root"]), checked_line(report, lang)]
    findings = report["findings"]
    if not findings:
        lines.append(m("scan_clean"))
        if report["stats"]["git"].startswith("skipped"):
            # --no-git: 깨끗한 결과가 색인까지 확인한 것으로 읽히지 않게 한다
            lines.append(m("scan_git_skipped_note"))
        elif nothing_here(report):
            # 빈 폴더 등: 에이전트가 직접 뒤지지 않고 바로 원인을 설명하고 폴더를 묻도록 이유를 함께 적는다
            lines.append(m("scan_nothing_here"))
            lines.append(m("scan_usual_cause"))
    else:
        lines.append(m("scan_found", n=len(findings), summary=summary_text(report["summary"])))
        lines.append("")
        for f in findings[:max_rows]:
            cls = "human" if f["kind"] in HUMAN_KINDS else "warning" if f["kind"] in WARN_KINDS else "fixable"
            shown = (f.get("guess") or {}).get("text") or f["path"]      # legacy zip names: show the decoded guess
            where = f.get("location") or (f"{f['zip']}:{shown}" if f["where"] == "zip" else f["path"])
            if "repo" in f:                                              # index entry of a repository below ROOT
                where += "  " + m("scan_in_repo", repo=f["repo"])
            lines.append(f"{f['kind']:<22} {f['where']:<5} [{m('kind_' + cls)}] {where}")
            if f["form"] == "collision":
                for mem in f["members"]:
                    extra = f"  blob={mem['blob']}" if "blob" in mem else ""
                    lines.append("    " + m("scan_members", hex=mem["path_hex"], extra=extra))
            else:
                lines.append("    " + m("scan_bytes", hex=f.get("raw_hex") or f["path_hex"]))
                if f["nfc_hex"] != f["path_hex"] and "raw_hex" not in f:
                    lines.append("    " + m("scan_nfc_bytes", hex=f["nfc_hex"]))
                if "disk_hex" in f:
                    lines.append("    " + m("scan_disk_bytes", hex=f["disk_hex"], name=f["disk"]))
                if f.get("guess", {}).get("encoding"):
                    lines.append("    " + m("scan_guess", enc=f["guess"]["encoding"], text=f["guess"]["text"]))
        if len(findings) > max_rows:
            # 나머지가 들어 있는 보고서 파일을 이미 썼다면 그 이름을 적는다
            hidden = len(findings) - max_rows
            lines.append(m("scan_more_out", n=hidden, total=len(findings), path=out_path) if out_path
                         else m("scan_more", n=hidden))
        lines.append("")
        lines.append(m("scan_legend"))
        for k in ALL_KINDS:
            if k in report["summary"]:
                lines.append(f"  {k}: {m('k_' + k)}")
    if report["stats"].get("skipped_dirs"):
        lines.append(m("scan_skipped", n=report["stats"]["skipped_dirs"]))
    for path in report["stats"].get("repos_unreadable", []):
        # git 이 열지 못한 .git: "문제 없음"만 보고 끝내지 않도록 눈에 띄게 알린다
        lines.append(m("scan_repo_unreadable", path=path))
    code = report["exit_code"]
    lines.append(m("scan_exit", code=code, meaning=m(f"scan_exit_{code}")))
    if code in (1, 3):
        # 계획 파일을 이미 썼다면 그 파일로 바로 다음 명령을 안내한다
        lines.append(m("scan_next_apply", plan=plan_path) if plan_path else m("scan_next_plan"))
    if code == 3:
        lines.append(m("scan_next_human"))
    if exit_zero and code:
        lines.append(m("scan_exit_zero_note"))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lang = n.lang_from_argv(argv)
    p = n.make_parser("scan.py", "scan_desc", lang)
    p.add_argument("root", metavar="ROOT", nargs="?", default=".", help=n.msg(lang, "scan_root"))
    p.add_argument("--out", metavar="FILE", help=n.msg(lang, "scan_out"))
    p.add_argument("--plan-out", metavar="FILE", help=n.msg(lang, "scan_plan_out"))
    p.add_argument("--zip", action="append", default=[], metavar="ZIP", help=n.msg(lang, "scan_zip"))
    p.add_argument("--no-git", action="store_true", help=n.msg(lang, "scan_no_git"))
    p.add_argument("--no-refs", action="store_true", help=n.msg(lang, "scan_no_refs"))
    p.add_argument("--ref-ext", default=DEFAULT_REF_EXT, metavar="EXTS", help=n.msg(lang, "scan_ref_ext", default=DEFAULT_REF_EXT))
    p.add_argument("--exclude", action="append", default=[], metavar="GLOB", help=n.msg(lang, "scan_exclude"))
    p.add_argument("--casefold", action="store_true", help=n.msg(lang, "scan_casefold"))
    p.add_argument("--json", action="store_true", help=n.msg(lang, "scan_json"))
    p.add_argument("--max-rows", type=int, default=200, metavar="N", help=n.msg(lang, "scan_max_rows"))
    p.add_argument("--exit-zero", action="store_true", help=n.msg(lang, "scan_exit_zero"))
    args = p.parse_args(argv)
    lang = n.pick_lang(args.lang)
    if args.max_rows < 0:
        # 음수는 슬라이스에서 뒤에서부터 잘라 행 수와 "N건 더" 숫자가 틀어진다
        p.error(n.msg(lang, "err_max_rows"))
    try:
        n.guard_output(args.out)
        n.guard_output(args.plan_out)
        report, plan = run_scan(args.root, args.zip, args.no_git, args.no_refs, args.ref_ext, args.exclude, args.casefold)
    except n.ToolError as e:
        sys.stderr.write(f"{n.msg(lang, 'err_prefix')}: {e.render(lang)}\n")
        return e.code
    notes = []
    try:
        if args.out:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(n.dumps(report))
            notes.append(n.msg(lang, "scan_report_written", path=args.out))
        if args.plan_out:
            with open(args.plan_out, "w", encoding="utf-8") as fh:
                fh.write(n.dumps(plan))
            notes.append(n.msg(lang, "scan_plan_written", path=args.plan_out, actions=len(plan["actions"]),
                               blocked=len(plan["blocked"])))
    except OSError as e:
        sys.stderr.write(f"{n.msg(lang, 'err_prefix')}: {e}\n")
        return 2
    if args.json:
        sys.stdout.write(n.dumps(report))
        for line in notes:
            sys.stderr.write(line + "\n")
    else:
        sys.stdout.write(render_table(report, lang, args.max_rows, args.exit_zero, args.plan_out, args.out))
        for line in notes:
            sys.stdout.write(line + "\n")
        # 마지막 줄: 출력이 전체 결과인지(다시 확인할 필요 없음), 잘렸다면 나머지가 어디 있는지 알린다
        sys.stdout.write(done_line(report, lang, args.max_rows, args.out) + "\n")
    # --exit-zero: 검사가 끝났으면 0 으로 끝낸다 (결과 코드는 보고서 exit_code 와 표 마지막 줄에 남는다)
    return 0 if args.exit_zero else report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
