"""AC-6: English and Korean messages, language precedence, language-independent JSON."""
from __future__ import annotations

import json
import re
import subprocess
import sys

import pytest

import _npd as n
from conftest import SCRIPTS, apply_mod, run_cli, scan_mod, show_mod


def placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{(\w+)\}", text))


def test_both_languages_have_the_same_keys_and_placeholders():
    en, ko = n.MESSAGES["en"], n.MESSAGES["ko"]
    assert set(en) == set(ko)
    for key in en:
        assert placeholders(en[key]) == placeholders(ko[key]), key


def test_every_scan_kind_has_a_description():
    import scan
    for lang in n.LANGS:
        for kind in scan.ALL_KINDS:
            assert n.msg(lang, "k_" + kind)


@pytest.mark.parametrize("env,expected", [
    ({}, "en"),
    ({"LANG": "ko_KR.UTF-8"}, "ko"),
    ({"LANG": "fr_FR.UTF-8"}, "en"),
    ({"LANG": "en_US.UTF-8", "LC_MESSAGES": "ko_KR.UTF-8"}, "ko"),
    ({"LC_MESSAGES": "en_US.UTF-8", "LC_ALL": "ko_KR.UTF-8"}, "ko"),
    ({"LC_ALL": "C", "LANG": "ko_KR.UTF-8"}, "en"),
    ({"LC_ALL": "ko_KR.UTF-8", "NFC_PATH_DOCTOR_LANG": "en"}, "en"),
    ({"NFC_PATH_DOCTOR_LANG": "ko", "LC_ALL": "en_US.UTF-8"}, "ko"),
])
def test_language_precedence_from_environment(env, expected):
    assert n.pick_lang(None, env) == expected


def test_cli_option_beats_everything():
    assert n.pick_lang("en", {"NFC_PATH_DOCTOR_LANG": "ko", "LANG": "ko_KR.UTF-8"}) == "en"
    assert n.pick_lang("ko", {"LANG": "en_US.UTF-8"}) == "ko"


def test_lang_from_argv_handles_both_spellings():
    assert n.lang_from_argv(["x", "--lang", "ko"], {}) == "ko"
    assert n.lang_from_argv(["x", "--lang=ko"], {}) == "ko"
    assert n.lang_from_argv(["x", "--lang", "zz"], {"LANG": "ko_KR"}) == "ko"      # invalid value falls back to the environment


@pytest.mark.parametrize("script", ["show.py", "scan.py", "apply.py"])
def test_help_is_localized(script):
    def helptext(lang):
        r = subprocess.run([sys.executable, str(SCRIPTS / script), "--help", "--lang", lang], capture_output=True, text=True)
        assert r.returncode == 0
        return r.stdout
    en, ko = helptext("en"), helptext("ko")
    assert not re.search(r"[가-힣]", en)
    assert len(re.findall(r"[가-힣]", ko)) > 20
    # 0.1.4: the argparse headings and the usage prefix follow the language too
    assert en.startswith("usage: ") and "\noptions:\n" in en
    assert ko.startswith("사용법: ") and "\n옵션:\n" in ko
    assert "usage:" not in ko and "options:" not in ko and "positional arguments" not in ko
    assert ("positional arguments:" in en) == ("위치 인자:" in ko)


def test_environment_variable_changes_the_output(site, capsys, monkeypatch):
    code, out, _ = run_cli(scan_mod, [str(site), "--no-git"], capsys, {"NFC_PATH_DOCTOR_LANG": "ko"}, monkeypatch)
    assert code == 1 and "발견 4건" in out
    code, out, _ = run_cli(scan_mod, [str(site), "--no-git"], capsys, {"LANG": "en_US.UTF-8"}, monkeypatch)
    assert code == 1 and "4 finding(s)" in out
    code, out, _ = run_cli(scan_mod, [str(site), "--no-git", "--lang", "en"], capsys, {"NFC_PATH_DOCTOR_LANG": "ko"}, monkeypatch)
    assert "4 finding(s)" in out


def test_json_is_identical_in_both_languages(site, capsys):
    outs = {}
    for lang in n.LANGS:
        code, out, _ = run_cli(scan_mod, [str(site), "--no-git", "--json", "--lang", lang], capsys)
        assert code == 1
        outs[lang] = out
    assert outs["en"] == outs["ko"]
    kinds = {f["kind"] for f in json.loads(outs["en"])["findings"]}
    assert kinds == {"nfd_name", "ref_mismatch"}


def test_error_messages_in_both_languages(tmp_path, capsys):
    missing = str(tmp_path / "nope")
    code, _, err = run_cli(scan_mod, [missing, "--lang", "ko"], capsys)
    assert code == 2 and "오류: 경로가 없습니다" in err
    code, _, err = run_cli(scan_mod, [missing, "--lang", "en"], capsys)
    assert code == 2 and "error: path not found" in err
    code, _, err = run_cli(show_mod, ["--lang", "ko"], capsys)
    assert code == 2 and "오류:" in err
    code, _, err = run_cli(apply_mod, ["--lang", "ko"], capsys)
    assert code == 2 and "오류:" in err


def test_usage_errors_use_the_localized_prefix(capsys):
    # ROOT 는 생략하면 현재 폴더다 (0.1.3): 잘못된 옵션 값으로 사용법 오류를 낸다
    with pytest.raises(SystemExit) as e:
        scan_mod.main(["--max-rows", "many", "--lang", "ko"])
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "오류:" in err and err.startswith("사용법: scan.py")


def test_ko_table_and_show_output(site, capsys):
    code, out, _ = run_cli(scan_mod, [str(site), "--no-git", "--lang", "ko"], capsys)
    assert "종류 설명:" in out and "수정 가능" in out and "README.md:3:9" in out
    code, out, _ = run_cli(show_mod, ["abc", "def", "--lang", "ko"], capsys)
    assert "바이트 동일: 아니오" in out and "NFC 로 접으면 동일: 아니오" in out
