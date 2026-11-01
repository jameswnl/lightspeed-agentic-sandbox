"""Tests for scripts/lint_baseline.py parsing and matching (no tools are run)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "lint_baseline.py"
_spec = importlib.util.spec_from_file_location("lint_baseline", _PATH)
lb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lb)

F401 = ("ruff", "src/a.py", "F401", "`os` imported but unused")
E501 = ("ruff", "src/b.py", "E501", "Line too long (101 > 100)")


def test_parse_mypy_strips_line_numbers_and_ignores_notes():
    out = (
        'src/a.py:12: error: Bad thing "x"  [assignment]\n'
        "src/a.py:12: note: some hint\n"
        "src/b.py:3:7: error: No code here\n"
        "Found 2 errors in 2 files (checked 5 source files)\n"
    )
    assert lb.parse_mypy(out) == [
        ("mypy", "src/a.py", "assignment", 'Bad thing "x"'),
        ("mypy", "src/b.py", "", "No code here"),
    ]


def test_parse_mypy_rejects_count_mismatch():
    with pytest.raises(SystemExit, match="parsed 0 mypy errors"):
        lb.parse_mypy("src/a.py: error: unparseable\nFound 1 error in 1 file\n")


def test_parse_ruff_format():
    out = "Would reformat: src/a.py\n1 file would be reformatted, 3 files already formatted\n"
    assert lb.parse_ruff_format(out) == [("ruff-format", "src/a.py", "format", "would reformat")]


def test_compare_clean_when_head_matches_baseline():
    result = lb.compare([F401, E501], [E501, F401], changed=set())
    assert not any(result.values())


def test_compare_counts_extra_occurrence_of_baselined_signature_as_new():
    result = lb.compare([F401, F401], [F401], changed=set())
    assert result["new"] == [F401]


def test_compare_flags_any_finding_in_changed_files():
    result = lb.compare([F401, E501], [F401, E501], changed={"src/a.py"})
    assert result["new"] == []
    assert result["in changed files"] == [F401]


def test_compare_flags_stale_baseline_entries():
    result = lb.compare([], [E501], changed=set())
    assert result["stale baseline"] == [E501]


def test_baseline_round_trip(tmp_path):
    path = tmp_path / "baseline.json"
    lb.write_baseline(path, [E501, F401, F401])
    assert lb.load_baseline(path) == [F401, F401, E501]
