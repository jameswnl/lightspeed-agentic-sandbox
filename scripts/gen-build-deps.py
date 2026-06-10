#!/usr/bin/env python3
"""
Generate requirements-build.txt for hermetic builds.

Scans the input requirements files for packages whose sdist hash is
present (meaning Cachi2 will prefetch the source tarball).  For each
such package, downloads the sdist from PyPI, reads build-system.requires
from pyproject.toml, and resolves the full build-dependency tree via
``uv pip compile`` to pinned ``name==version`` lines.

Wheel prefetching is controlled by the ``binary`` filter in the Tekton
pipeline config, not by hashes in this file.

Usage:
    python scripts/gen-build-deps.py requirements-build.txt \\
        requirements.x86_64.txt requirements.aarch64.txt
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore[no-redef]

PYPI_JSON = "https://pypi.org/pypi/{}/{}/json"

HEADER = (
    "#\n"
    "# Build dependencies for hermetic builds (auto-generated).\n"
    "# Needed when Cachi2 prefetches a source distribution instead of a wheel.\n"
    "# Regenerate: make requirements\n"
    "#\n"
)


def _norm(name: str) -> str:
    """PEP 503 name normalization."""
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_packages(*paths: str) -> dict[str, tuple[str, str]]:
    """Return {normalized_name: (raw_name, version)} from requirements files."""
    pkgs: dict[str, tuple[str, str]] = {}
    for p in paths:
        with open(p) as fh:
            for line in fh:
                m = re.match(r"^([A-Za-z0-9][\w.-]*)==([^\s\\;]+)", line.strip())
                if m:
                    pkgs.setdefault(_norm(m.group(1)), (m.group(1), m.group(2)))
    return pkgs


def parse_hashes(*paths: str) -> dict[str, set[str]]:
    """Return {normalized_name: {sha256_digest, …}} from requirements files."""
    hashes: dict[str, set[str]] = {}
    current: str | None = None
    for p in paths:
        with open(p) as fh:
            for line in fh:
                m = re.match(r"^([A-Za-z0-9][\w.-]*)==([^\s\\;]+)", line.strip())
                if m:
                    current = _norm(m.group(1))
                    hashes.setdefault(current, set())
                if current:
                    for h in re.findall(r"--hash=sha256:([0-9a-f]+)", line):
                        hashes[current].add(h)
    return hashes


def _pypi_urls(name: str, version: str) -> list[dict]:
    url = PYPI_JSON.format(name, version)
    if not url.startswith("https://"):
        return []
    req = urllib.request.Request(url, headers={"Accept": "application/json"})  # noqa: S310
    with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310
        return json.loads(r.read()).get("urls", [])


def _has_sdist_hash(urls: list[dict], our_hashes: set[str]) -> bool:
    """True if any of our requirement hashes matches a source distribution."""
    for u in urls:
        fn = u["filename"]
        if fn.endswith((".tar.gz", ".zip")):
            digest = u.get("digests", {}).get("sha256", "")
            if digest in our_hashes:
                return True
    return False


def _sdist_build_requires(urls: list[dict]) -> list[str]:
    """Download the first sdist and return its build-system.requires."""
    for u in urls:
        if not u["filename"].endswith(".tar.gz"):
            continue
        if not u["url"].startswith("https://"):
            continue
        try:
            with urllib.request.urlopen(u["url"], timeout=60) as r:  # noqa: S310
                raw = r.read()
            with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
                for m in tf.getmembers():
                    if re.match(r"^[^/]+/pyproject\.toml$", m.name):
                        fobj = tf.extractfile(m)
                        if fobj:
                            data = tomllib.loads(fobj.read().decode())
                            return data.get("build-system", {}).get("requires", ["setuptools"])
                return ["setuptools"]
        except Exception as exc:
            print(f"  warn: {u['filename']}: {exc}", file=sys.stderr)
    return []


def main() -> None:
    if len(sys.argv) < 3:
        print(
            f"Usage: {sys.argv[0]} OUTPUT REQ_FILE [REQ_FILE …]",
            file=sys.stderr,
        )
        sys.exit(1)

    output, *req_files = sys.argv[1:]
    runtime_pkgs = parse_packages(*req_files)
    all_hashes = parse_hashes(*req_files)

    print(
        f"Scanning {len(runtime_pkgs)} packages for prefetched sdists …",
        file=sys.stderr,
    )

    build_specs: list[str] = []
    for norm_name, (name, ver) in sorted(runtime_pkgs.items()):
        our_hashes = all_hashes.get(norm_name, set())
        if not our_hashes:
            continue
        try:
            urls = _pypi_urls(name, ver)
        except Exception as exc:
            print(f"  warn: {name}=={ver}: {exc}", file=sys.stderr)
            continue
        if not _has_sdist_hash(urls, our_hashes):
            continue
        reqs = _sdist_build_requires(urls)
        if reqs:
            print(f"  {name}=={ver} → {reqs}", file=sys.stderr)
            build_specs.extend(reqs)

    # Deduplicate by normalized name, keeping the first spec seen
    seen: set[str] = set()
    unique: list[str] = []
    for spec in build_specs:
        m = re.match(r"([A-Za-z0-9][\w.-]*)", spec)
        if not m:
            continue
        n = _norm(m.group(1))
        if n not in seen:
            seen.add(n)
            unique.append(spec)

    if not unique:
        Path(output).write_text(HEADER)
        print("No build dependencies needed.", file=sys.stderr)
        return

    with tempfile.NamedTemporaryFile(mode="w", suffix=".in", delete=False) as tmp:
        for spec in sorted(unique):
            tmp.write(spec + "\n")
        tmp_path = tmp.name

    result = subprocess.run(
        ["uv", "pip", "compile", tmp_path, "--no-header", "--no-annotate"],
        capture_output=True,
        text=True,
    )
    Path(tmp_path).unlink(missing_ok=True)

    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        sys.exit(1)

    Path(output).write_text(HEADER + result.stdout)
    pkg_count = sum(
        1 for line in result.stdout.splitlines() if re.match(r"^[A-Za-z0-9][\w.-]*==", line)
    )
    print(f"Wrote {output} ({pkg_count} packages)", file=sys.stderr)


if __name__ == "__main__":
    main()
