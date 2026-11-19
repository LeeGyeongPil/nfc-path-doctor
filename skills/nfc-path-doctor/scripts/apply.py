#!/usr/bin/env python3
"""Run a plan made by scan.py --plan-out: dry-run by default, --apply to rename, re-index and rewrite references.

Exit codes: 0 ok (dry-run included), 1 plan is stale (nothing changed), 2 input error, 4 a step failed and the
steps already done were rolled back.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _npd as n  # noqa: E402
import scan  # noqa: E402

KNOWN_OPS = {"fs_rename", "index_rename", "ref_rewrite"}
Step = dict


# ---------------------------------------------------------------------------
# plan file
# ---------------------------------------------------------------------------


class _PlanProblem(ValueError):
    """A plan-file problem; ``key`` is a message key so that the detail is shown in the chosen language."""

    def __init__(self, key: str, **kw: object):
        super().__init__(key)
        self.key = key
        self.kw = kw


def _hexval(v: object, what: str, relative: bool = False) -> bytes:
    if not isinstance(v, str):
        raise _PlanProblem("plan_hex_str", what=what)
    try:
        raw = bytes.fromhex(v)
    except ValueError as e:
        raise _PlanProblem("plan_hex_invalid", what=what) from e
    if relative and (raw.startswith(b"/") or b".." in raw.split(b"/")):
        raise _PlanProblem("plan_path_escape", what=what)
    return raw


def load_plan(path: str) -> dict:
    try:
        with open(path, "rb") as fh:
            plan = json.loads(fh.read().decode("utf-8"))
    except OSError as e:
        raise n.ToolError("err_file_missing", path=n.disp(path)) from e
    except (ValueError, UnicodeDecodeError) as e:
        raise n.ToolError("err_plan_invalid", path=n.disp(path), detail=n.ToolError("plan_bad_json")) from e
    try:
        if not isinstance(plan, dict) or plan.get("version") != n.REPORT_VERSION:
            raise _PlanProblem("plan_bad_version")
        if plan.get("target") not in ("NFC", "undo"):
            raise _PlanProblem("plan_bad_target")
        _hexval(plan.get("root_hex"), "root_hex")
        if not isinstance(plan.get("actions"), list):
            raise _PlanProblem("plan_actions_list")
        for i, a in enumerate(plan["actions"]):
            if not isinstance(a, dict) or a.get("op") not in KNOWN_OPS:
                raise _PlanProblem("plan_unknown_op", i=i)
            if a["op"] == "fs_rename":
                _hexval(a.get("parent_hex"), f"actions[{i}].parent_hex", True)
                for k in ("from_hex", "to_hex"):
                    raw = _hexval(a.get(k), f"actions[{i}].{k}")
                    if b"/" in raw:
                        raise _PlanProblem("plan_path_escape", what=f"actions[{i}].{k}")
            elif a["op"] == "index_rename":
                for k in ("from_hex", "to_hex"):
                    _hexval(a.get(k), f"actions[{i}].{k}")
                if "repo_hex" in a:
                    _hexval(a["repo_hex"], f"actions[{i}].repo_hex", True)
                if not isinstance(a.get("mode"), str) or not isinstance(a.get("blob"), str):
                    raise _PlanProblem("plan_need_mode_blob", i=i)
            else:
                _hexval(a.get("file_hex"), f"actions[{i}].file_hex", True)
                if not isinstance(a.get("sha256"), str) or not isinstance(a.get("occurrences"), list):
                    raise _PlanProblem("plan_need_sha_occ", i=i)
                for o in a["occurrences"]:
                    if not isinstance(o, dict):
                        raise _PlanProblem("plan_need_line_col", i=i)
                    for k in ("old_hex", "new_hex"):
                        _hexval(o.get(k), f"actions[{i}].occurrences.{k}")
                    if not isinstance(o.get("line"), int) or not isinstance(o.get("col"), int):
                        raise _PlanProblem("plan_need_line_col", i=i)
    except _PlanProblem as e:
        raise n.ToolError("err_plan_invalid", path=n.disp(path), detail=n.ToolError(e.key, **e.kw)) from e
    return plan


# ---------------------------------------------------------------------------
# text rewriting
# ---------------------------------------------------------------------------


def rewrite_text(text: str, occs: list[dict]) -> tuple[str, list[dict]] | str:
    """Replace each occurrence; return (new text, inverse occurrences) or the 1-based line that does not match."""
    lines = text.split("\n")
    by_line: dict[int, list[dict]] = {}
    for o in occs:
        by_line.setdefault(o["line"], []).append(o)
    inverse: list[dict] = []
    for ln, items in by_line.items():
        if ln < 1 or ln > len(lines):
            return str(ln)
        line = lines[ln - 1]
        out, pos, delta = [], 0, 0
        for o in sorted(items, key=lambda x: x["col"]):
            old = bytes.fromhex(o["old_hex"]).decode("utf-8")
            new = bytes.fromhex(o["new_hex"]).decode("utf-8")
            start = o["col"] - 1
            if start < pos or line[start:start + len(old)] != old:
                return str(ln)
            out.append(line[pos:start])
            out.append(new)
            inverse.append({"line": ln, "col": o["col"] + delta, "old_hex": o["new_hex"], "new_hex": o["old_hex"]})
            delta += len(new) - len(old)
            pos = start + len(old)
        out.append(line[pos:])
        lines[ln - 1] = "".join(out)
    inverse.sort(key=lambda x: (x["line"], x["col"]))
    return "\n".join(lines), inverse


# ---------------------------------------------------------------------------
# state checks (before anything is changed)
# ---------------------------------------------------------------------------


def _join(root_b: bytes, rel_hex: str) -> bytes:
    rel = bytes.fromhex(rel_hex)
    return os.path.join(root_b, rel) if rel else root_b


def toplevel(root_b: bytes) -> str:
    r = n.git_bytes(os.fsdecode(root_b), ["rev-parse", "--show-toplevel"])
    if r.returncode != 0:
        raise n.ToolError("err_git", detail=r.stderr.decode("utf-8", "replace").strip()[:200])
    return os.fsdecode(r.stdout.rstrip(b"\n"))


def repo_top(root_b: bytes, st: Step, real=lambda p: p) -> str:
    """Work tree top of the repository an index step belongs to (a repository below the root has repo_hex)."""
    path = real(_join(root_b, st.get("repo_hex", "")))
    if st.get("repo_hex") and not os.path.isdir(path):
        # 저장소 폴더 이름 자체가 NFD 였다면 앞선 fs_rename 이 이미 NFC 로 바꿨다
        path = real(os.path.join(root_b, n.encode_name(n.nfc(n.decode_name(bytes.fromhex(st["repo_hex"]))))))
    return toplevel(path)


def check_state(root_b: bytes, steps: list[Step]) -> list[tuple[str, dict]]:
    """Return stale reasons as (message key, format arguments); an empty list means the plan still fits.

    The steps are simulated in order: a rename makes the new name (and everything below it) visible to the
    later steps, so an undo plan that renames a parent first and then its children is checked correctly on
    file systems that do not fold NFC and NFD together.
    """
    reasons: list[tuple[str, dict]] = []
    if not os.path.isdir(root_b):
        return [("stale_root", {"path": n.disp(n.decode_name(root_b))})]
    moved: dict[bytes, bytes] = {}                    # path after a simulated rename -> path that exists now
    listings: dict[bytes, set[bytes]] = {}            # simulated directory contents

    def real(path: bytes) -> bytes:
        for k in sorted(moved, key=len, reverse=True):
            if path == k or path.startswith(k + b"/"):
                return moved[k] + path[len(k):]
        return path

    indexes: dict[str, tuple[set[tuple[str, str, bytes]], set[bytes]]] = {}   # repo_hex -> (entries, paths)
    for st in steps:
        op = st["op"]
        if op == "fs_rename":
            parent = _join(root_b, st["parent_hex"])
            src, dst = bytes.fromhex(st["from_hex"]), bytes.fromhex(st["to_hex"])
            if parent not in listings:
                try:
                    listings[parent] = set(os.listdir(real(parent)))
                except OSError:
                    reasons.append(("stale_missing", {"what": "what_entry", "path": n.disp(n.decode_name(parent))}))
                    continue
            names = listings[parent]
            if src not in names:
                reasons.append(("stale_missing", {"what": "what_entry", "path": n.disp(n.decode_name(os.path.join(parent, src)))}))
            elif dst in names and dst != src:
                reasons.append(("stale_target_exists", {"path": n.disp(n.decode_name(os.path.join(parent, dst)))}))
            else:
                old_full, new_full = os.path.join(parent, src), os.path.join(parent, dst)
                if old_full != new_full:
                    names.discard(src)
                    names.add(dst)
                    moved[new_full] = real(old_full)
                    moved.pop(old_full, None)
                    for k in [k for k in listings if k == old_full or k.startswith(old_full + b"/")]:
                        listings[new_full + k[len(old_full):]] = listings.pop(k)
        elif op == "index_rename":
            key = st.get("repo_hex", "")
            if key not in indexes:
                # 저장소마다 색인을 한 번만 읽는다
                try:
                    ents = n.git_index(repo_top(root_b, st, real))
                except n.GitUnavailable as e:
                    raise n.ToolError("err_git", detail=str(e)) from e
                entries = {(m, b, p) for m, b, s, p in ents if s == 0}
                indexes[key] = (entries, {p for _, _, p in entries})
            index, index_paths = indexes[key]
            src, dst = bytes.fromhex(st["from_hex"]), bytes.fromhex(st["to_hex"])
            if (st["mode"], st["blob"], src) not in index:
                reasons.append(("stale_index_missing", {"path": n.disp(n.decode_name(src))}))
            elif dst in index_paths and dst != src:
                reasons.append(("stale_index_target", {"path": n.disp(n.decode_name(dst))}))
        else:
            fpath = _join(root_b, st["file_hex"])
            try:
                with open(real(fpath), "rb") as fh:
                    data = fh.read()
            except OSError:
                reasons.append(("stale_missing", {"what": "what_file", "path": n.disp(n.decode_name(fpath))}))
                continue
            if hashlib.sha256(data).hexdigest() != st["sha256"]:
                reasons.append(("stale_hash", {"path": n.disp(n.decode_name(fpath))}))
                continue
            res = rewrite_text(data.decode("utf-8", "surrogateescape"), st["occurrences"])
            if isinstance(res, str):
                reasons.append(("stale_ref_text", {"path": n.disp(n.decode_name(fpath)), "line": res}))
    return reasons


# ---------------------------------------------------------------------------
# running one step; every step returns its inverse
# ---------------------------------------------------------------------------


def run_step(root_b: bytes, st: Step) -> Step:
    op = st["op"]
    if op == "fs_rename":
        parent = _join(root_b, st["parent_hex"])
        src, dst = bytes.fromhex(st["from_hex"]), bytes.fromhex(st["to_hex"])
        os.rename(os.path.join(parent, src), os.path.join(parent, dst))
        if dst not in os.listdir(parent):
            raise n.ToolError("err_fs_kept_form", code=4, path=n.disp(n.decode_name(os.path.join(parent, dst))))
        return {**st, "from_hex": st["to_hex"], "to_hex": st["from_hex"]}
    if op == "index_rename":
        src, dst = bytes.fromhex(st["from_hex"]), bytes.fromhex(st["to_hex"])
        zeros = b"0" * len(st["blob"])
        payload = (b"0 " + zeros + b"\t" + src + b"\0" + st["mode"].encode() + b" " + st["blob"].encode() + b"\t" + dst + b"\0")
        r = n.git_bytes(repo_top(root_b, st), ["update-index", "-z", "--index-info"], payload)
        if r.returncode != 0:
            raise n.ToolError("err_git", code=4, detail=r.stderr.decode("utf-8", "replace").strip()[:200])
        return {**st, "from_hex": st["to_hex"], "to_hex": st["from_hex"]}
    fpath = _join(root_b, st["file_hex"])
    with open(fpath, "rb") as fh:
        data = fh.read()
    if hashlib.sha256(data).hexdigest() != st["sha256"]:
        raise n.ToolError("stale_hash", code=1, path=n.disp(n.decode_name(fpath)))
    res = rewrite_text(data.decode("utf-8"), st["occurrences"])
    if isinstance(res, str):
        raise n.ToolError("stale_ref_text", code=1, path=n.disp(n.decode_name(fpath)), line=res)
    new_text, inverse = res
    new_data = new_text.encode("utf-8")
    tmp = fpath + b".npd-tmp"
    with open(tmp, "wb") as fh:
        fh.write(new_data)
    shutil.copymode(fpath, tmp)
    os.replace(tmp, fpath)
    return {**st, "sha256": hashlib.sha256(new_data).hexdigest(), "occurrences": inverse}


def describe_step(st: Step, lang: str) -> str:
    op = st["op"]
    if op == "fs_rename":
        parent = n.decode_name(bytes.fromhex(st["parent_hex"]))
        return n.msg(lang, "step_fs_rename", parent=n.disp(parent + "/") if parent else "",
                     src=n.disp(n.decode_name(bytes.fromhex(st["from_hex"]))),
                     dst=n.disp(n.decode_name(bytes.fromhex(st["to_hex"]))))
    if op == "index_rename":
        text = n.msg(lang, "step_index_rename", src=n.disp(n.decode_name(bytes.fromhex(st["from_hex"]))),
                     dst=n.disp(n.decode_name(bytes.fromhex(st["to_hex"]))))
        if st.get("repo_hex"):
            text += "  " + n.msg(lang, "scan_in_repo", repo=n.disp(n.decode_name(bytes.fromhex(st["repo_hex"]))))
        return text
    return n.msg(lang, "step_ref_rewrite", file=n.disp(n.decode_name(bytes.fromhex(st["file_hex"]))),
                 n=len(st["occurrences"]))


def undo_plan(plan: dict, inverses: list[Step]) -> dict:
    return {"version": n.REPORT_VERSION, "target": "undo", "root": plan["root"], "root_hex": plan["root_hex"],
            "git_prefix_hex": plan.get("git_prefix_hex"), "options": plan.get("options", {}),
            "actions": list(reversed(inverses)), "blocked": []}


def recheck(plan: dict, lang: str) -> str:
    opts = plan.get("options") or {}
    root = n.decode_name(bytes.fromhex(plan["root_hex"]))
    report, _ = scan.run_scan(root, opts.get("zips", []), bool(opts.get("no_git")), bool(opts.get("no_refs")),
                              ",".join(opts.get("ref_ext") or [scan.DEFAULT_REF_EXT]), opts.get("exclude", []),
                              bool(opts.get("casefold")))
    summary = scan.summary_text(report["summary"]) or n.msg(lang, "apply_recheck_clean")
    return n.msg(lang, "apply_recheck", code=report["exit_code"], summary=summary)


def guard_pending_undo(undo_out: str | None, plan_path: str) -> None:
    """Refuse to replace an undo file that still holds the only record for changes not yet reverted."""
    if not undo_out or not os.path.isfile(undo_out):
        return
    try:
        if os.path.samefile(undo_out, plan_path):
            return   # the undo file being applied right now is consumed by this run
    except OSError:
        return
    try:
        old = load_plan(undo_out)
    except n.ToolError:
        return
    if old.get("target") != "undo" or not old["actions"]:
        return
    if check_state(bytes.fromhex(old["root_hex"]), old["actions"]):
        return   # already used (steps no longer match the disk): safe to overwrite
    raise n.ToolError("err_undo_pending", path=n.disp(undo_out))


def write_json(path: str, obj: object) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(n.dumps(obj))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def run(args: object, lang: str) -> int:
    out = sys.stdout
    err = sys.stderr
    plan_path = getattr(args, "undo") or getattr(args, "plan")
    plan = load_plan(plan_path)
    root_b = bytes.fromhex(plan["root_hex"])
    steps: list[Step] = plan["actions"]
    for b in plan.get("blocked", []):
        out.write(n.msg(lang, "apply_blocked", name=b.get("key", "?")) + "\n")
    if not steps:
        out.write(n.msg(lang, "apply_nothing") + "\n")
        return 0
    reasons = check_state(root_b, steps)
    if reasons:
        for i, (key, kw) in enumerate(reasons[:10]):
            err.write(f"{n.msg(lang, 'err_prefix')}: " + n.msg(lang, "err_plan_stale", detail=n.msg(lang, key, **{
                k: (n.msg(lang, v) if k == "what" else v) for k, v in kw.items()})) + "\n")
        err.write(n.msg(lang, "err_plan_stale_hint") + "\n")
        return 1
    if not getattr(args, "apply"):
        out.write(n.msg(lang, "apply_dry_header", n=len(steps)) + "\n")
        for st in steps:
            out.write("  " + describe_step(st, lang) + "\n")
        out.write(n.msg(lang, "apply_dry_hint") + "\n")
        return 0
    undo_out = getattr(args, "undo_out") or (None if getattr(args, "undo") else
                                             os.path.join(os.path.dirname(os.path.abspath(plan_path)), "undo.json"))
    n.guard_output(undo_out)   # fail before anything is renamed, not after
    guard_pending_undo(undo_out, plan_path)
    out.write(n.msg(lang, "apply_run_header", n=len(steps)) + "\n")
    done: list[Step] = []
    try:
        for st in steps:
            done.append(run_step(root_b, st))
            out.write("  " + describe_step(st, lang) + "\n")
    except (OSError, n.ToolError, n.GitUnavailable) as e:
        detail = e.render(lang) if isinstance(e, n.ToolError) else str(e)
        code = e.code if isinstance(e, n.ToolError) and e.code == 1 else 4
        err.write(f"{n.msg(lang, 'err_prefix')}: {n.msg(lang, 'err_apply_failed', detail=detail)}\n")
        remaining = list(done)
        try:
            while remaining:
                run_step(root_b, remaining[-1])
                remaining.pop()
        except (OSError, n.ToolError, n.GitUnavailable) as e2:
            path = undo_out or "undo.json"
            write_json(path, undo_plan(plan, remaining))
            err.write(f"{n.msg(lang, 'err_prefix')}: " + n.msg(lang, "err_rollback_failed", detail=str(e2), path=path) + "\n")
            return 4
        err.write(n.msg(lang, "err_rolled_back", n=len(done)) + "\n")
        return code
    out.write(n.msg(lang, "apply_done", n=len(done)) + "\n")
    if undo_out:
        write_json(undo_out, undo_plan(plan, done))
        out.write(n.msg(lang, "apply_undo_written", path=undo_out) + "\n")
    out.write(recheck(plan, lang) + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lang = n.lang_from_argv(argv)
    p = n.make_parser("apply.py", "apply_desc", lang)
    p.add_argument("plan", nargs="?", metavar="PLAN", help=n.msg(lang, "apply_plan"))
    p.add_argument("--apply", action="store_true", help=n.msg(lang, "apply_apply"))
    p.add_argument("--undo-out", metavar="FILE", help=n.msg(lang, "apply_undo_out"))
    p.add_argument("--undo", metavar="UNDO", help=n.msg(lang, "apply_undo"))
    args = p.parse_args(argv)
    lang = n.pick_lang(args.lang)
    try:
        if bool(args.plan) == bool(args.undo):
            raise n.ToolError("err_plan_or_undo")
        return run(args, lang)
    except n.ToolError as e:
        sys.stderr.write(f"{n.msg(lang, 'err_prefix')}: {e.render(lang)}\n")
        return e.code
    except n.GitUnavailable as e:
        sys.stderr.write(f"{n.msg(lang, 'err_prefix')}: {n.msg(lang, 'err_git', detail=str(e))}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
