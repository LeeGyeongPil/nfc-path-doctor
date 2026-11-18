#!/usr/bin/env python3
"""Show the Unicode form, code points and UTF-8 bytes of names, and compare several names byte by byte."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _npd as n  # noqa: E402


def describe(text: str, label: str, kind: str) -> dict:
    cps = n.codepoints(text)
    return {
        "label": label,
        "kind": kind,
        "text": n.disp(text),
        "form": n.classify(text),
        "len_cp": len(text),
        "codepoints": cps,
        "utf8_hex": n.hexs(text),
        "nfc_hex": n.hexs(n.nfc(text)),
        "nfd_hex": n.hexs(n.nfd(text)),
    }


def resolve_on_disk(typed: str) -> str:
    """Return ``typed`` with every component replaced by the name bytes really stored on disk.

    The lookup compares NFC keys, so an NFC name typed by a person or a model finds the NFD file and shows its
    true bytes. Raises ToolError when a component does not exist or several entries share one NFC key.
    """
    if not typed:
        raise n.ToolError("err_path_not_found", path=n.disp(typed))
    parts = typed.split("/")
    cur = "/" if typed.startswith("/") else "."
    out: list[str] = []
    for i, comp in enumerate(parts):
        if comp in ("", "."):
            if comp == "." or (comp == "" and i == 0):
                out.append(comp)
            continue
        if comp == "..":
            cur = os.path.join(cur, comp)
            out.append(comp)
            continue
        try:
            names = [n.decode_name(x) for x in os.listdir(n.encode_name(cur))]
        except OSError as e:
            raise n.ToolError("err_path_not_found", path=n.disp(typed)) from e
        hits = [x for x in names if n.nfc(x) == n.nfc(comp)]
        if not hits:
            raise n.ToolError("err_path_not_found", path=n.disp(typed))
        if len(hits) > 1:
            raise n.ToolError("err_ambiguous", path=n.disp(cur))
        out.append(hits[0])
        cur = os.path.join(cur, hits[0])
    joined = "/".join(out)
    return "/" + joined.lstrip("/") if typed.startswith("/") else joined


def collect(args: object) -> list[dict]:
    records: list[dict] = []
    for i, t in enumerate(getattr(args, "text")):
        records.append(describe(t, f"arg{i + 1}", "text"))
    if getattr(args, "file"):
        fp = getattr(args, "file")
        try:
            with open(fp, "rb") as fh:
                data = fh.read()
        except OSError as e:
            raise n.ToolError("err_file_missing", path=n.disp(fp)) from e
        lines = data.decode("utf-8", "surrogateescape").split("\n")
        if lines and lines[-1] == "":
            lines.pop()
        for i, line in enumerate(lines):
            records.append(describe(line.rstrip("\r"), f"line{i + 1}", "line"))
    for p in getattr(args, "path"):
        disk = resolve_on_disk(p)
        rec = describe(disk, f"path:{n.disp(p)}", "path")
        rec["typed_hex"] = n.hexs(p)
        records.append(rec)
    if not records:
        raise n.ToolError("err_no_input")
    return records


def compare(records: list[dict]) -> dict:
    if len(records) < 2:
        return {}
    return {
        "same_bytes": len({r["utf8_hex"] for r in records}) == 1,
        "same_nfc": len({r["nfc_hex"] for r in records}) == 1,
    }


def render(records: list[dict], cmp: dict, lang: str) -> str:
    yn = lambda v: n.msg(lang, "yes" if v else "no")  # noqa: E731
    lines = []
    for i, r in enumerate(records, 1):
        lines.append(n.msg(lang, "show_form_line", n=i, form=r["form"], cps=r["len_cp"], nbytes=len(r["utf8_hex"]) // 2))
        lines.append("     " + n.msg(lang, "form_" + r["form"]))
        for lbl, key in (("show_lbl_text", "text"), ("show_lbl_cp", None), ("show_lbl_utf8", "utf8_hex"),
                         ("show_lbl_nfc", "nfc_hex"), ("show_lbl_nfd", "nfd_hex")):
            val = " ".join(r["codepoints"]) if key is None else r[key]
            lines.append(n.msg(lang, "show_label", label=n.msg(lang, lbl), value=val))
        if "typed_hex" in r:
            lines.append(n.msg(lang, "show_label", label=n.msg(lang, "show_lbl_typed"), value=r["typed_hex"]))
    if cmp:
        lines.append(n.msg(lang, "show_same_bytes", v=yn(cmp["same_bytes"])))
        lines.append(n.msg(lang, "show_same_nfc", v=yn(cmp["same_nfc"])))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    lang = n.lang_from_argv(argv)
    p = n.make_parser("show.py", "show_desc", lang)
    p.add_argument("text", nargs="*", default=[], metavar="TEXT", help=n.msg(lang, "show_text"))
    p.add_argument("--file", metavar="FILE", help=n.msg(lang, "show_file"))
    p.add_argument("--path", action="append", default=[], metavar="PATH", help=n.msg(lang, "show_path"))
    p.add_argument("--json", action="store_true", help=n.msg(lang, "show_json"))
    p.add_argument("--out", metavar="FILE", help=n.msg(lang, "show_out"))
    args = p.parse_args(argv)
    lang = n.pick_lang(args.lang)
    try:
        n.guard_output(args.out)
        records = collect(args)
    except n.ToolError as e:
        sys.stderr.write(f"{n.msg(lang, 'err_prefix')}: {e.render(lang)}\n")
        return e.code
    cmp = compare(records)
    doc = n.dumps({"version": n.REPORT_VERSION, "inputs": records, **cmp})
    if args.out:
        try:
            with open(args.out, "w", encoding="utf-8") as fh:
                fh.write(doc)
        except OSError as e:
            sys.stderr.write(f"{n.msg(lang, 'err_prefix')}: {e}\n")
            return 2
    if args.json:
        sys.stdout.write(doc)
    else:
        sys.stdout.write(render(records, cmp, lang))
    return 0


if __name__ == "__main__":
    sys.exit(main())
