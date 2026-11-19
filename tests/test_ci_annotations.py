"""CI diagnostics: the test session leaves GitHub Actions annotations that say what failed without the job log."""
from __future__ import annotations

from types import SimpleNamespace

from conftest import annotations_enabled, ci_annotations, gh_command, gh_escape


def test_annotations_only_in_github_actions_or_when_asked():
    assert annotations_enabled({"GITHUB_ACTIONS": "true"})
    assert annotations_enabled({"NPD_TEST_ANNOTATIONS": "1"})
    assert not annotations_enabled({}) and not annotations_enabled({"GITHUB_ACTIONS": "false"})


def report(nodeid: str, when: str = "call", location=("tests/test_x.py", 9, "test_x"), longrepr=None):
    return SimpleNamespace(nodeid=nodeid, when=when, location=location, longrepr=longrepr)


def test_workflow_command_values_are_escaped():
    assert gh_escape("50%\nnext\r") == "50%25%0Anext%0D"
    assert gh_escape("a:b,c", prop=True) == "a%3Ab%2Cc"
    assert gh_command("error", "x\ny", file="tests/a.py", line=3, title="failed: t::a[b,c]") == \
        "::error file=tests/a.py,line=3,title=failed%3A t%3A%3Aa[b%2Cc]::x%0Ay"
    assert gh_command("notice", "plain") == "::notice::plain"


def test_failures_become_errors_on_their_line():
    crash = SimpleNamespace(reprcrash=SimpleNamespace(message="AssertionError: assert 1 == 3"))
    stats = {"failed": [report("tests/test_x.py::test_x", longrepr=crash)], "passed": [report("tests/test_x.py::ok")]}
    lines = ci_annotations(stats, "linux", "3.12.0")
    assert lines[0] == ("::error file=tests/test_x.py,line=10,title=failed%3A tests/test_x.py%3A%3Atest_x"
                        "::AssertionError: assert 1 == 3")
    assert lines[1] == "::notice title=pytest summary::linux Python 3.12.0: 1 passed, 1 failed, 0 error, 0 skipped"
    assert len(lines) == 2


def test_linux_only_skips_warn_on_linux_but_not_on_macos():
    skip = report("tests/test_scan_fs.py::test_fs_collision_when_both_forms_coexist",
                  longrepr=("tests/test_scan_fs.py", 103, "Skipped: this file system folds NFC and NFD names (APFS)"))
    other = report("tests/test_scan_fs.py::test_unreadable_folder_is_counted",
                   longrepr=("tests/test_scan_fs.py", 138, "Skipped: running with privileges that ignore permissions"))
    stats = {"skipped": [skip, other], "warnings": [SimpleNamespace(message="w")]}   # warning reports are not tests
    linux = ci_annotations(stats, "linux", "3.10.0")
    assert "2 skipped" in linux[0] and "%0ASKIPPED tests/test_scan_fs.py::test_fs_collision" in linux[0]
    assert linux[1].startswith("::warning title=Linux-only tests did not run::")
    assert "test_fs_collision_when_both_forms_coexist" in linux[1] and "unreadable" not in linux[1]
    mac = ci_annotations(stats, "darwin", "3.10.0")
    assert len(mac) == 1 and mac[0].startswith("::notice ")
