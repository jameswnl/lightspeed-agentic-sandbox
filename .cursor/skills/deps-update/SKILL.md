---
name: deps-update
description: >-
  Update Python dependencies with uv, regenerate lock and Konflux requirements
  files, then verify lint, types, and tests. Fix breakage from API changes in
  bumped packages. Use when the user says "deps update", "bump dependencies",
  or "update deps".
---

# deps-update

This repository uses `uv`, `uv.lock`, and `make requirements` to produce
`requirements.x86_64.txt` and `requirements.aarch64.txt` for Konflux hermetic
builds (see `CLAUDE.md` / `AGENTS.md`).

## Step 1: Snapshot Current State

Before changing anything:

1. Fetch and use a clean base (adjust remote name: `upstream` vs `origin`):
   ```bash
   git fetch upstream   # or: git fetch origin
   git checkout upstream/main   # or: origin/main
   git checkout -b chore/deps-update
   ```
   If the working tree has uncommitted changes, **stop and tell the user** to
   commit or stash first.
2. Run `uv lock --check` to confirm the lock file is consistent.
3. Run `uv run python --version` to record the Python version.

## Step 2: Bump Dependencies

If a specific package was requested, bump only that package:

```bash
uv lock --upgrade-package {package}
uv sync --all-extras
```

Otherwise bump everything:

```bash
uv lock --upgrade
uv sync --all-extras
```

Capture the output of `uv lock --upgrade` — it lists version changes for the
commit message and report.

Then regenerate pinned requirements for both architectures:

```bash
make requirements
```

## Step 3: Verify — Format, Lint, Types

```bash
make format
make verify
```

If verification fails, fix calling code to match new APIs (do not pin old
versions unless there is a confirmed upstream regression). Re-run until clean.

## Step 4: Verify — Unit Tests

```bash
make test
```

Same triage as in the original deps-update skill: distinguish API-induced test
breakage from real regressions.

## Step 5: Report, Commit, and PR

```bash
git diff --name-only
```

**If only** `pyproject.toml`, `uv.lock`, and `requirements.*.txt` changed —
you may commit with a clear message after summarizing bumps for the user.

**If source or test files changed** — wait for user acknowledgment before
committing. Then use **raise-pr** to open the PR.

Typical commit for deps-only:

```bash
git add pyproject.toml uv.lock requirements.x86_64.txt requirements.aarch64.txt
git commit -m "chore: bump dependencies"
```

## Constraints

- Clean tree required at the start of a broad bump.
- Prefer fix-forward over pinning.
- Do not hand-edit `requirements.*.txt` — always regenerate via `make requirements`.
- Eval/live tests are optional for a deps PR unless the user asks; default gate
  is `make verify` + `make test`.
