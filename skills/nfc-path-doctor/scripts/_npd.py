"""Shared helpers for nfc-path-doctor: name forms, byte/str conversion, git access, language and messages.

Internal module (leading underscore) - the public interface is show.py, scan.py and apply.py.
Names are handled as ``str`` decoded with ``surrogateescape`` so that invalid UTF-8 survives a round trip.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import unicodedata
from typing import Iterable, Mapping, NoReturn

LANGS = ("en", "ko")
ENV_LANG = "NFC_PATH_DOCTOR_LANG"
REPORT_VERSION = 1

# ---------------------------------------------------------------------------
# language selection
# ---------------------------------------------------------------------------


def pick_lang(cli: str | None = None, env: Mapping[str, str] | None = None) -> str:
    """--lang > NFC_PATH_DOCTOR_LANG > LC_ALL > LC_MESSAGES > LANG. Anything that is not Korean is English."""
    env = os.environ if env is None else env
    for cand in (cli, env.get(ENV_LANG), env.get("LC_ALL"), env.get("LC_MESSAGES"), env.get("LANG")):
        if cand:
            return "ko" if cand.lower().startswith("ko") else "en"
    return "en"


def lang_from_argv(argv: list[str], env: Mapping[str, str] | None = None) -> str:
    """Find --lang before argparse runs so that --help can already be localized."""
    cli = None
    for i, a in enumerate(argv):
        if a == "--lang" and i + 1 < len(argv):
            cli = argv[i + 1]
        elif a.startswith("--lang="):
            cli = a.split("=", 1)[1]
    if cli not in LANGS:
        cli = None
    return pick_lang(cli, env)


MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "err_prefix": "error",
        "lang_help": "output language: en or ko (default: NFC_PATH_DOCTOR_LANG, then the locale)",
        "help_help": "show this help message and exit",
        # errors
        "err_path_not_found": "path not found: {path}",
        "err_not_dir": "not a directory: {path}",
        "err_zip_invalid": "not a valid zip file: {path} (re-download or re-create the archive)",
        "err_git": "git failed: {detail} (use --no-git to skip the index check)",
        "err_file_missing": "file not found: {path}",
        "err_no_input": "nothing to show: pass TEXT arguments, --file FILE or --path PATH",
        "err_ambiguous": "ambiguous name in {path}: several entries share the same NFC form (run scan.py)",
        "err_plan_invalid": "invalid plan file {path}: {detail} (create it with scan.py --plan-out)",
        "err_plan_stale": "plan is stale: {detail}",
        "err_plan_stale_hint": "nothing was changed. Run scan.py again to create a fresh plan.",
        "err_apply_failed": "step failed: {detail}",
        "err_rolled_back": "the steps that had already run were rolled back ({n} step(s)).",
        "err_rollback_failed": "rollback of a step failed: {detail}; the undo file lists what remains: {path}",
        "err_plan_or_undo": "give exactly one of PLAN or --undo UNDO",
        "err_out_exists": "refusing to overwrite {path}: it exists and is not a file written by these scripts "
                          "(pass another file name, or delete or move it first)",
        "plan_bad_json": "not valid JSON",
        "plan_bad_version": "unsupported or missing version",
        "plan_bad_target": "target must be NFC or undo",
        "plan_actions_list": "actions must be a list",
        "plan_unknown_op": "actions[{i}] has an unknown op",
        "plan_need_mode_blob": "actions[{i}] needs mode and blob",
        "plan_need_sha_occ": "actions[{i}] needs sha256 and occurrences",
        "plan_need_line_col": "actions[{i}] occurrence needs line and col",
        "plan_hex_str": "{what} must be a hex string",
        "plan_hex_invalid": "{what} is not valid hex",
        "plan_path_escape": "{what} points outside the scanned folder (absolute path or '..')",
        "err_fs_kept_form": "the file system did not keep the NFC name for {path} (HFS+ stores names decomposed)",
        # show.py
        "show_desc": "Show the Unicode form (NFC, NFD, ...), code points and UTF-8 bytes of names, "
                     "and whether several names are the same bytes or only the same after NFC.",
        "show_text": "text to inspect (a file name or any string)",
        "show_file": "read the inputs from FILE, one per line",
        "show_path": "existing path; shows the name bytes exactly as stored on disk (repeatable)",
        "show_json": "print JSON instead of text",
        "show_out": "also write the JSON result to this file",
        "show_form_line": "[{n}] form={form}  code points={cps}  UTF-8 bytes={nbytes}",
        "show_label": "     {label}: {value}",
        "show_lbl_text": "text",
        "show_lbl_cp": "code points",
        "show_lbl_utf8": "UTF-8 hex",
        "show_lbl_nfc": "NFC hex",
        "show_lbl_nfd": "NFD hex",
        "show_lbl_typed": "typed hex",
        "show_same_bytes": "same bytes: {v}",
        "show_same_nfc": "same after NFC: {v}",
        "yes": "yes",
        "no": "no",
        "form_ascii": "ASCII only (no form issue)",
        "form_nfc": "precomposed (NFC)",
        "form_nfd": "decomposed (NFD)",
        "form_neutral": "same in NFC and NFD (no composable characters)",
        "form_mixed": "mixed: neither pure NFC nor pure NFD",
        "form_undecodable": "contains bytes that are not valid UTF-8",
        # scan.py
        "scan_desc": "Compare file names on disk, the git index, zip archives and text references byte by byte "
                     "and report Unicode normalization (NFC/NFD) mismatches.",
        "scan_root": "folder to scan",
        "scan_out": "write the JSON report to this file",
        "scan_plan_out": "write a dry-run NFC fix plan (JSON) for apply.py to this file",
        "scan_zip": "also inspect this zip archive (repeatable; never modified)",
        "scan_no_git": "skip the git index check",
        "scan_no_refs": "skip the text reference check",
        "scan_ref_ext": "comma separated extensions to search for references (default: {default})",
        "scan_exclude": "skip entries matching this glob (repeatable; .git is always skipped)",
        "scan_casefold": "also warn about names that differ only by case after NFC",
        "scan_json": "print the JSON report to stdout instead of the table",
        "scan_max_rows": "maximum findings printed in the table (default 200; the JSON is never cut)",
        "scan_exit_zero": "exit 0 whenever the scan finished, even with findings (the result code stays in the "
                          "report's exit_code and the last table line); input errors still exit 2",
        "scan_exit_zero_note": "(--exit-zero: the process exit status is 0; the result code above is in report "
                               "exit_code)",
        "scan_in_repo": "(git repository: {repo})",
        "scan_title": "nfc-path-doctor scan  root={root}",
        "scan_clean": "No normalization problems found.",
        "scan_found": "{n} finding(s): {summary}",
        "scan_more": "... {n} more finding(s) not shown (see --out for all)",
        "scan_legend": "What the kinds mean:",
        "scan_bytes": "bytes: {hex}",
        "scan_nfc_bytes": "as NFC: {hex}",
        "scan_disk_bytes": "on disk: {hex}  ({name})",
        "scan_guess": "guess ({enc}): {text}",
        "scan_members": "member: {hex}{extra}",
        "scan_exit": "exit code {code}: {meaning}",
        "scan_exit_0": "clean",
        "scan_exit_1": "fixable findings (see --plan-out)",
        "scan_exit_3": "needs a human decision (collision, conflict or undecodable name)",
        "scan_next_plan": "Next: add --plan-out plan.json, then run apply.py plan.json (dry-run) and ask before --apply.",
        "scan_next_apply": "Next: apply.py {plan} shows the steps (dry-run); add --apply only when the user asked for "
                           "the fix.",
        "scan_next_human": "Collisions are never fixed automatically: ask which entry to keep.",
        "scan_plan_written": "plan written: {path} ({actions} action(s), {blocked} blocked)",
        "scan_report_written": "report written: {path}",
        "scan_skipped": "note: {n} unreadable folder(s) were skipped",
        "scan_repo_unreadable": "warning: {path} has a .git entry that git cannot open, so its index was NOT "
                                "checked (unpacked only partly? HEAD, objects, refs or index missing)",
        "k_nfd_name": "name on disk is decomposed (NFD); other systems may not find it by its NFC name",
        "k_mixed_name": "name on disk mixes NFC and NFD characters",
        "k_undecodable_name": "name is not valid UTF-8; it cannot be normalized automatically",
        "k_fs_collision": "two entries in one folder differ only by NFC/NFD form (needs a human decision)",
        "k_case_collision": "warning: names differ only by case after NFC",
        "k_index_nfd_path": "git index path is not NFC",
        "k_index_form_mismatch": "git index path differs in form from the same file on disk",
        "k_index_collision": "git index holds the same path in two forms (needs a human decision)",
        "k_index_conflict_stage": "index entry is in a merge-conflict stage; resolve the merge first",
        "k_zip_nfd_name": "zip entry name is decomposed (NFD) or mixed",
        "k_zip_legacy_name": "zip entry has no UTF-8 flag; its non-ASCII name is read in a legacy encoding",
        "k_zip_collision": "zip holds the same name in two forms (needs a human decision)",
        "k_ref_mismatch": "text reference differs in bytes from the real file name (works on macOS, may break on Linux)",
        "kind_fixable": "fixable",
        "kind_human": "decision",
        "kind_warning": "warning",
        # apply.py
        "apply_desc": "Run a plan made by scan.py: dry-run by default, --apply to change names, git index and "
                      "references, with an undo file.",
        "apply_plan": "plan JSON written by scan.py --plan-out",
        "apply_apply": "really apply (without it nothing is changed)",
        "apply_undo_out": "where to write the undo file (default: undo.json next to the plan)",
        "apply_undo": "undo a previous run with its undo file (dry-run unless --apply)",
        "apply_dry_header": "dry-run: {n} step(s) would run, nothing was changed",
        "apply_run_header": "applying {n} step(s)",
        "apply_nothing": "nothing to do: the plan has no actions",
        "apply_blocked": "blocked (not touched, needs a human decision): {name}",
        "apply_done": "done: {n} step(s) applied",
        "apply_undo_written": "undo file written: {path}",
        "apply_recheck": "re-check: exit {code}, {summary}",
        "apply_recheck_clean": "no findings",
        "apply_dry_hint": "Run again with --apply to change anything.",
        "step_fs_rename": "rename   {parent}{src}  ->  {dst}",
        "step_index_rename": "index    {src}  ->  {dst}",
        "step_ref_rewrite": "rewrite  {file}  ({n} reference(s))",
        "stale_missing": "{what} no longer exists: {path}",
        "stale_target_exists": "target already exists: {path}",
        "stale_index_missing": "index entry changed or missing: {path}",
        "stale_index_target": "index already has the target path: {path}",
        "stale_hash": "file content changed since the plan: {path}",
        "stale_ref_text": "reference text moved or changed in {path} line {line}",
        "stale_root": "root folder is missing: {path}",
        "what_entry": "entry",
        "what_file": "file",
    },
    "ko": {
        "err_prefix": "오류",
        "lang_help": "출력 언어: en 또는 ko (기본값: NFC_PATH_DOCTOR_LANG, 그다음 로캘)",
        "help_help": "도움말을 보여 주고 끝냅니다",
        "err_path_not_found": "경로가 없습니다: {path}",
        "err_not_dir": "디렉터리가 아닙니다: {path}",
        "err_zip_invalid": "올바른 zip 파일이 아닙니다: {path} (압축 파일을 다시 받거나 다시 만드세요)",
        "err_git": "git 실행에 실패했습니다: {detail} (색인 검사를 건너뛰려면 --no-git)",
        "err_file_missing": "파일이 없습니다: {path}",
        "err_no_input": "보여 줄 입력이 없습니다: TEXT 인자, --file FILE 또는 --path PATH 를 주세요",
        "err_ambiguous": "{path} 안에 NFC 형태가 같은 항목이 여러 개라 하나로 정할 수 없습니다 (scan.py 를 실행하세요)",
        "err_plan_invalid": "계획 파일이 올바르지 않습니다 {path}: {detail} (scan.py --plan-out 으로 만드세요)",
        "err_plan_stale": "계획이 오래되었습니다(stale): {detail}",
        "err_plan_stale_hint": "아무것도 바꾸지 않았습니다. scan.py 를 다시 실행해 새 계획을 만드세요.",
        "err_apply_failed": "단계 실패: {detail}",
        "err_rolled_back": "이미 실행한 단계를 되돌렸습니다 ({n}단계).",
        "err_rollback_failed": "단계 되돌리기에 실패했습니다: {detail}; 남은 일은 undo 파일에 있습니다: {path}",
        "err_plan_or_undo": "PLAN 과 --undo UNDO 중 정확히 하나만 주세요",
        "err_out_exists": "{path} 을(를) 덮어쓰지 않습니다: 이미 있고 이 스크립트가 쓴 파일이 아닙니다 "
                          "(다른 파일 이름을 주거나, 먼저 지우거나 옮기세요)",
        "plan_bad_json": "올바른 JSON 이 아닙니다",
        "plan_bad_version": "지원하지 않거나 빠진 version 입니다",
        "plan_bad_target": "target 은 NFC 또는 undo 여야 합니다",
        "plan_actions_list": "actions 는 목록이어야 합니다",
        "plan_unknown_op": "actions[{i}] 의 op 를 알 수 없습니다",
        "plan_need_mode_blob": "actions[{i}] 에는 mode 와 blob 이 필요합니다",
        "plan_need_sha_occ": "actions[{i}] 에는 sha256 과 occurrences 가 필요합니다",
        "plan_need_line_col": "actions[{i}] 의 occurrence 에는 line 과 col 이 필요합니다",
        "plan_hex_str": "{what} 는 16진수 문자열이어야 합니다",
        "plan_hex_invalid": "{what} 가 올바른 16진수가 아닙니다",
        "plan_path_escape": "{what} 가 검사한 폴더 밖을 가리킵니다 (절대 경로 또는 '..')",
        "err_fs_kept_form": "파일 시스템이 {path} 의 NFC 이름을 유지하지 않았습니다 (HFS+ 는 이름을 분해형으로 저장합니다)",
        "show_desc": "이름의 유니코드 형태(NFC, NFD 등)·코드포인트·UTF-8 바이트를 보여 주고, 여러 이름이 "
                     "바이트까지 같은지 NFC 로 접어야 같은지 알려 줍니다.",
        "show_text": "확인할 텍스트 (파일 이름이나 아무 문자열)",
        "show_file": "FILE 에서 한 줄에 하나씩 읽습니다",
        "show_path": "있는 경로; 디스크에 저장된 이름 바이트를 그대로 보여 줍니다 (여러 번 가능)",
        "show_json": "텍스트 대신 JSON 으로 출력",
        "show_out": "JSON 결과를 이 파일에도 씁니다",
        "show_form_line": "[{n}] 형태={form}  코드포인트={cps}개  UTF-8 {nbytes}바이트",
        "show_label": "     {label}: {value}",
        "show_lbl_text": "텍스트",
        "show_lbl_cp": "코드포인트",
        "show_lbl_utf8": "UTF-8 hex",
        "show_lbl_nfc": "NFC hex",
        "show_lbl_nfd": "NFD hex",
        "show_lbl_typed": "입력한 hex",
        "show_same_bytes": "바이트 동일: {v}",
        "show_same_nfc": "NFC 로 접으면 동일: {v}",
        "yes": "예",
        "no": "아니오",
        "form_ascii": "ASCII 만 (형태 문제 없음)",
        "form_nfc": "조합형 (NFC)",
        "form_nfd": "분해형 (NFD)",
        "form_neutral": "NFC 와 NFD 가 같음 (조합되는 글자 없음)",
        "form_mixed": "혼합: 순수 NFC 도 순수 NFD 도 아님",
        "form_undecodable": "올바른 UTF-8 이 아닌 바이트 포함",
        "scan_desc": "디스크의 파일 이름, git 색인, zip 파일, 텍스트 참조를 바이트 단위로 대조해 "
                     "유니코드 정규화(NFC/NFD) 불일치를 보고합니다.",
        "scan_root": "검사할 폴더",
        "scan_out": "JSON 보고서를 이 파일에 씁니다",
        "scan_plan_out": "apply.py 가 쓸 NFC 수정 계획(JSON, dry-run 용)을 이 파일에 씁니다",
        "scan_zip": "이 zip 도 검사합니다 (여러 번 가능, 절대 수정하지 않음)",
        "scan_no_git": "git 색인 검사를 건너뜁니다",
        "scan_no_refs": "텍스트 참조 검사를 건너뜁니다",
        "scan_ref_ext": "참조를 찾을 확장자(쉼표로 구분, 기본값: {default})",
        "scan_exclude": "이 glob 과 맞는 항목을 건너뜁니다 (여러 번 가능, .git 은 항상 건너뜀)",
        "scan_casefold": "NFC 로 접은 뒤 대소문자만 다른 이름도 경고합니다",
        "scan_json": "표 대신 JSON 보고서를 표준 출력에 냅니다",
        "scan_max_rows": "표에 보여 줄 최대 발견 수 (기본 200, JSON 은 자르지 않음)",
        "scan_exit_zero": "발견이 있어도 검사가 끝나면 0 으로 끝냅니다 (결과 코드는 보고서 exit_code 와 표 마지막 줄에 "
                          "남음). 입력 오류는 그대로 2",
        "scan_exit_zero_note": "(--exit-zero: 프로세스 종료 상태는 0 입니다. 위 결과 코드는 보고서 exit_code 에 있습니다)",
        "scan_in_repo": "(git 저장소: {repo})",
        "scan_title": "nfc-path-doctor 검사  루트={root}",
        "scan_clean": "정규화 문제가 없습니다.",
        "scan_found": "발견 {n}건: {summary}",
        "scan_more": "... {n}건 더 있습니다 (전체는 --out 참고)",
        "scan_legend": "종류 설명:",
        "scan_bytes": "바이트: {hex}",
        "scan_nfc_bytes": "NFC 로: {hex}",
        "scan_disk_bytes": "디스크: {hex}  ({name})",
        "scan_guess": "추정 ({enc}): {text}",
        "scan_members": "구성원: {hex}{extra}",
        "scan_exit": "종료 코드 {code}: {meaning}",
        "scan_exit_0": "깨끗함",
        "scan_exit_1": "고칠 수 있는 발견 (--plan-out 참고)",
        "scan_exit_3": "사람의 결정이 필요함 (충돌·충돌 단계·디코드 불가 이름)",
        "scan_next_plan": "다음: --plan-out plan.json 을 붙여 실행하고, apply.py plan.json (dry-run) 으로 확인한 뒤 --apply 는 승인을 받고 실행하세요.",
        "scan_next_apply": "다음: apply.py {plan} 로 단계를 확인하세요 (dry-run). --apply 는 사용자가 수정을 요청했을 때만 붙입니다.",
        "scan_next_human": "충돌은 자동으로 고치지 않습니다: 어느 쪽을 남길지 물어보세요.",
        "scan_plan_written": "계획 저장: {path} (실행 {actions}건, 보류 {blocked}건)",
        "scan_report_written": "보고서 저장: {path}",
        "scan_skipped": "참고: 읽을 수 없는 폴더 {n}개를 건너뛰었습니다",
        "scan_repo_unreadable": "경고: {path} 에 .git 이 있지만 git 이 열지 못해 색인을 검사하지 못했습니다 "
                                "(압축이 일부만 풀렸나요? HEAD·objects·refs·index 누락)",
        "k_nfd_name": "디스크의 이름이 분해형(NFD)이라 다른 시스템에서 NFC 이름으로 못 찾을 수 있음",
        "k_mixed_name": "디스크의 이름에 NFC 와 NFD 글자가 섞여 있음",
        "k_undecodable_name": "이름이 올바른 UTF-8 이 아니라 자동으로 정규화할 수 없음",
        "k_fs_collision": "한 폴더 안의 두 항목이 NFC/NFD 형태만 다름 (사람의 결정 필요)",
        "k_case_collision": "경고: NFC 로 접은 뒤 대소문자만 다른 이름",
        "k_index_nfd_path": "git 색인 경로가 NFC 가 아님",
        "k_index_form_mismatch": "git 색인 경로가 디스크의 같은 파일과 형태가 다름",
        "k_index_collision": "git 색인에 같은 경로가 두 형태로 들어 있음 (사람의 결정 필요)",
        "k_index_conflict_stage": "색인 항목이 머지 충돌 단계임; 먼저 머지를 끝내세요",
        "k_zip_nfd_name": "zip 항목 이름이 분해형(NFD)이거나 혼합임",
        "k_zip_legacy_name": "zip 항목에 UTF-8 플래그가 없어 비ASCII 이름을 옛 인코딩으로 읽음",
        "k_zip_collision": "zip 안에 같은 이름이 두 형태로 들어 있음 (사람의 결정 필요)",
        "k_ref_mismatch": "텍스트 참조가 실제 파일 이름과 바이트가 다름 (macOS 에서는 열리지만 Linux 에서는 깨질 수 있음)",
        "kind_fixable": "수정 가능",
        "kind_human": "결정 필요",
        "kind_warning": "경고",
        "apply_desc": "scan.py 가 만든 계획을 실행합니다: 기본은 dry-run, --apply 를 주면 이름·git 색인·"
                      "참조를 바꾸고 undo 파일을 남깁니다.",
        "apply_plan": "scan.py --plan-out 이 만든 계획 JSON",
        "apply_apply": "실제로 적용합니다 (없으면 아무것도 바꾸지 않음)",
        "apply_undo_out": "undo 파일을 쓸 위치 (기본: 계획 파일 옆 undo.json)",
        "apply_undo": "이전 실행의 undo 파일로 되돌립니다 (--apply 가 없으면 dry-run)",
        "apply_dry_header": "dry-run: {n}단계를 실행할 예정이며 아무것도 바꾸지 않았습니다",
        "apply_run_header": "{n}단계를 적용합니다",
        "apply_nothing": "할 일이 없습니다: 계획에 실행할 항목이 없습니다",
        "apply_blocked": "보류 (건드리지 않음, 사람의 결정 필요): {name}",
        "apply_done": "완료: {n}단계 적용",
        "apply_undo_written": "undo 파일 저장: {path}",
        "apply_recheck": "재검사: 종료 코드 {code}, {summary}",
        "apply_recheck_clean": "발견 없음",
        "apply_dry_hint": "실제로 바꾸려면 --apply 를 붙여 다시 실행하세요.",
        "step_fs_rename": "이름 변경  {parent}{src}  ->  {dst}",
        "step_index_rename": "색인      {src}  ->  {dst}",
        "step_ref_rewrite": "참조 치환  {file}  (참조 {n}곳)",
        "stale_missing": "{what} 이(가) 더 이상 없습니다: {path}",
        "stale_target_exists": "대상 이름이 이미 있습니다: {path}",
        "stale_index_missing": "색인 항목이 바뀌었거나 없습니다: {path}",
        "stale_index_target": "색인에 대상 경로가 이미 있습니다: {path}",
        "stale_hash": "계획을 만든 뒤 파일 내용이 바뀌었습니다: {path}",
        "stale_ref_text": "{path} 의 {line}번째 줄에서 참조 텍스트가 옮겨졌거나 바뀌었습니다",
        "stale_root": "루트 폴더가 없습니다: {path}",
        "what_entry": "항목",
        "what_file": "파일",
    },
}


def msg(lang: str, key: str, **kw: object) -> str:
    return MESSAGES[lang][key].format(**kw)


class ToolError(Exception):
    """A user-facing error: a message key plus format arguments, rendered in the chosen language by the caller.

    ``code`` is the process exit code.
    """

    def __init__(self, key: str, code: int = 2, **kw: object):
        super().__init__(key)
        self.key = key
        self.kw = kw
        self.code = code

    def render(self, lang: str) -> str:
        # a keyword value may itself be a ToolError (a localized detail sentence)
        kw = {k: (v.render(lang) if isinstance(v, ToolError) else v) for k, v in self.kw.items()}
        return msg(lang, self.key, **kw)


class LocalizedParser(argparse.ArgumentParser):
    """argparse parser whose usage errors use the selected language prefix and exit 2."""

    def __init__(self, *a: object, lang: str = "en", **kw: object):
        self.lang = lang
        super().__init__(*a, **kw)  # type: ignore[arg-type]

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        sys.stderr.write(f"{msg(self.lang, 'err_prefix')}: {message}\n")
        raise SystemExit(2)


def make_parser(prog: str, desc_key: str, lang: str) -> LocalizedParser:
    p = LocalizedParser(prog=prog, description=msg(lang, desc_key), add_help=False, lang=lang)
    p.add_argument("-h", "--help", action="help", help=msg(lang, "help_help"))
    p.add_argument("--lang", choices=LANGS, default=None, help=msg(lang, "lang_help"))
    return p


# ---------------------------------------------------------------------------
# names and forms
# ---------------------------------------------------------------------------


def decode_name(b: bytes) -> str:
    return b.decode("utf-8", "surrogateescape")


def encode_name(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def has_undecodable(s: str) -> bool:
    return any("\udc80" <= c <= "\udcff" for c in s)


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def nfd(s: str) -> str:
    return unicodedata.normalize("NFD", s)


def classify(s: str) -> str:
    """ascii | nfc | nfd | neutral | mixed | undecodable."""
    if has_undecodable(s):
        return "undecodable"
    if s.isascii():
        return "ascii"
    is_c = unicodedata.is_normalized("NFC", s)
    is_d = unicodedata.is_normalized("NFD", s)
    if is_c and is_d:
        return "neutral"
    if is_c:
        return "nfc"
    if is_d:
        return "nfd"
    return "mixed"


def hexs(s: str) -> str:
    return encode_name(s).hex()


def disp(s: str) -> str:
    """Printable/JSON-safe text for a name: invalid bytes become backslash escapes (the hex field keeps the truth)."""
    return encode_name(s).decode("utf-8", "backslashreplace")


def codepoints(s: str) -> list[str]:
    return [f"U+{ord(c):04X}" for c in s]


def sha256_file(path: bytes) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dumps(obj: object) -> str:
    """Deterministic JSON: sorted keys, fixed indent, no ASCII escaping."""
    return json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def join_rel(parent: str, name: str) -> str:
    return name if not parent else parent + "/" + name


# ---------------------------------------------------------------------------
# git access (read-only except update-index in apply.py)
# ---------------------------------------------------------------------------


class GitUnavailable(Exception):
    pass


def git_bytes(root: str, args: list[str], input_bytes: bytes | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["git", "-C", root, *args], input=input_bytes, capture_output=True, timeout=300)
    except FileNotFoundError as e:
        raise GitUnavailable(str(e)) from e
    except subprocess.TimeoutExpired as e:
        raise ToolError("err_git", detail="timeout") from e


def git_available() -> bool:
    """True when a git binary can be started."""
    try:
        return git_bytes(".", ["--version"]).returncode == 0
    except GitUnavailable:
        return False


def git_prefix(root: str) -> bytes | None:
    """Prefix of root inside its repository (b'' at the top), or None if root is not in a work tree / git missing."""
    try:
        r = git_bytes(root, ["rev-parse", "--is-inside-work-tree"])
    except GitUnavailable:
        return None
    if r.returncode != 0 or r.stdout.strip() != b"true":
        return None
    p = git_bytes(root, ["rev-parse", "--show-prefix"])
    if p.returncode != 0:
        return None
    return p.stdout.rstrip(b"\n")


def git_index(root: str) -> list[tuple[str, str, int, bytes]]:
    """Index entries under root as (mode, blob, stage, full path from the repository top)."""
    r = git_bytes(root, ["ls-files", "-z", "--stage", "--full-name"])
    if r.returncode != 0:
        raise ToolError("err_git", detail=r.stderr.decode("utf-8", "replace").strip()[:200])
    out = []
    for rec in r.stdout.split(b"\0"):
        if not rec:
            continue
        head, _, path = rec.partition(b"\t")
        mode, blob, stage = head.decode("ascii").split(" ")
        out.append((mode, blob, int(stage), path))
    return out


def sorted_unique(items: Iterable[str]) -> list[str]:
    return sorted(set(items), key=encode_name)


# ---------------------------------------------------------------------------
# output file guard
# ---------------------------------------------------------------------------


def is_own_output(name: str, text: str) -> bool:
    """True for a JSON report, plan or undo file written by scan.py / apply.py, or a show.py --out file."""
    if not name.lower().endswith(".json") or '"version"' not in text:
        return False
    try:
        data = json.loads(text)
    except ValueError:
        return False
    return (isinstance(data, dict) and data.get("version") == REPORT_VERSION
            and ("root_hex" in data or ("inputs" in data and "same_nfc" in data)))


def guard_output(path: str | None) -> None:
    """Refuse to overwrite an existing file that these scripts did not write (exit 2 with a way forward)."""
    if not path or not os.path.lexists(path):
        return
    try:
        with open(path, "rb") as fh:
            text = fh.read(50_000_000).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        text = ""
    if os.path.isfile(path) and is_own_output(os.path.basename(path), text):
        return
    raise ToolError("err_out_exists", path=disp(path))
