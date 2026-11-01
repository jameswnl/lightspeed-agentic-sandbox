#!/usr/bin/env python3
"""Fail on ruff/mypy diagnostics that are not in the committed baseline.

Runs the same checks as ``make verify`` (ruff check, ruff format --check, mypy)
and compares their diagnostics against ``scripts/lint_baseline.json``.

A diagnostic's signature is (tool, file, code, message). Line numbers are left
out so unrelated edits do not churn the baseline; matching is a multiset, so a
second occurrence of a baselined signature still counts as new. The check fails
when:

- head has a diagnostic not covered by the baseline,
- a file in --changed-files has any diagnostic at all (baselined or not),
- the baseline lists a diagnostic that no longer occurs (refresh it so fixed
  findings cannot silently come back).

Regenerate the baseline with ``--write-baseline`` and review the diff.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "scripts" / "lint_baseline.json"
MYPY_TARGETS = ["src/lightspeed_agentic", "scripts/lint_baseline.py"]

Signature = tuple[str, str, str, str]

_MYPY_ERROR = re.compile(
    r"^(?P<file>[^:\n]+):\d+(?::\d+)?: error: (?P<msg>.*?)(?:  \[(?P<code>[a-z0-9-]+)\])?$"
)
_MYPY_FOUND = re.compile(r"^Found (\d+) errors? in \d+ files?")


def _run(cmd: list[str], ok_codes: tuple[int, ...]) -> str:
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    if proc.returncode not in ok_codes:
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"{' '.join(cmd)} exited {proc.returncode}")
    return proc.stdout + proc.stderr


def parse_ruff_check(output: str) -> list[Signature]:
    sigs = []
    for item in json.loads(output):
        path = Path(item["filename"]).resolve().relative_to(ROOT).as_posix()
        sigs.append(("ruff", path, item["code"] or "syntax-error", item["message"]))
    return sigs


def parse_ruff_format(output: str) -> list[Signature]:
    prefix = "Would reformat: "
    return [
        ("ruff-format", line[len(prefix) :].strip(), "format", "would reformat")
        for line in output.splitlines()
        if line.startswith(prefix)
    ]


def parse_mypy(output: str) -> list[Signature]:
    sigs: list[Signature] = []
    found: int | None = None
    for line in output.splitlines():
        if m := _MYPY_ERROR.match(line):
            sigs.append(("mypy", m["file"], m["code"] or "", m["msg"]))
        elif m := _MYPY_FOUND.match(line):
            found = int(m[1])
    # Guard against parser drift: the count must match mypy's own summary.
    if (found or 0) != len(sigs):
        raise SystemExit(f"parsed {len(sigs)} mypy errors but mypy reported {found}")
    return sigs


def collect(raw_dir: Path | None) -> list[Signature]:
    py = sys.executable
    check = _run([py, "-m", "ruff", "check", ".", "--output-format=json"], (0, 1))
    fmt = _run([py, "-m", "ruff", "format", ".", "--check"], (0, 1))
    mypy = _run([py, "-m", "mypy", *MYPY_TARGETS], (0, 1))
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / "ruff-check.json").write_text(check)
        (raw_dir / "ruff-format.txt").write_text(fmt)
        (raw_dir / "mypy.txt").write_text(mypy)
    return parse_ruff_check(check) + parse_ruff_format(fmt) + parse_mypy(mypy)


def load_baseline(path: Path) -> list[Signature]:
    return [(e["tool"], e["file"], e["code"], e["message"]) for e in json.loads(path.read_text())]


def write_baseline(path: Path, sigs: list[Signature]) -> None:
    keys = ("tool", "file", "code", "message")
    lines = [json.dumps(dict(zip(keys, s, strict=True))) for s in sorted(sigs)]
    path.write_text("[\n  " + ",\n  ".join(lines) + "\n]\n" if lines else "[]\n")


def compare(
    head: list[Signature], baseline: list[Signature], changed: set[str]
) -> dict[str, list[Signature]]:
    head_c, base_c = Counter(head), Counter(baseline)
    return {
        "new": sorted((head_c - base_c).elements()),
        "in changed files": sorted(s for s in head if s[1] in changed),
        "stale baseline": sorted((base_c - head_c).elements()),
    }


def _report(head: list[Signature], problems: dict[str, list[Signature]]) -> str:
    failed = any(problems.values())
    out = [
        "## Lint baseline: " + ("FAILED" if failed else "no new findings"),
        f"{len(head)} current diagnostics (all baselined unless listed below).",
    ]
    for title, sigs in problems.items():
        if sigs:
            out.append(f"\n### {title} ({len(sigs)})")
            out.extend(f"- `{t}` {f} `{c}`: {m}" for t, f, c, m in sigs)
    if problems["stale baseline"]:
        out.append("\nRun `uv run python scripts/lint_baseline.py --write-baseline`.")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail on lint/type findings not in the baseline.")
    parser.add_argument("--write-baseline", action="store_true")
    parser.add_argument("--changed-files", type=Path, help="file listing changed paths")
    parser.add_argument("--raw-dir", type=Path, help="directory for raw tool output")
    args = parser.parse_args(argv)

    head = collect(args.raw_dir)
    if args.write_baseline:
        write_baseline(BASELINE, head)
        print(f"wrote {len(head)} entries to {BASELINE.relative_to(ROOT)}")
        return 0

    changed = set()
    if args.changed_files:
        changed = {p for p in args.changed_files.read_text().splitlines() if p}
    problems = compare(head, load_baseline(BASELINE), changed)
    report = _report(head, problems)
    print(report)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as fh:
            fh.write(report)
    return 1 if any(problems.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
