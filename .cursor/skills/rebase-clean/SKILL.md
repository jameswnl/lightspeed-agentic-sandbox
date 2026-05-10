---
name: rebase-clean
description: Performs a strict clean rebase of a feature branch onto main with minimal conflict resolution and full validation. Use when the user asks to rebase carefully and run tests + verify until green.
disable-model-invocation: true
---

# Clean Rebase Workflow

Use this workflow for rebases in **lightspeed-agentic-sandbox**.

## Rules

- Do not create extra temporary branches unless the user explicitly asks.
- Do not make unrelated edits.
- Finish rebase and validation; keep output brief.
- Never assume target branch names; detect current branch first.

## Step 0: Detect Branch Context

1. Run `git branch --show-current`.
2. Confirm with the user before reset/rebase.
3. Restate current branch, target base (`main`), and approved baseline ref when
   anything is ambiguous.

## Step 1: Restore Branch Baseline (Rerun Only)

Use only after a failed rebase. Otherwise skip to Step 2.

## Step 2: Rebase Onto Main

```bash
git fetch origin   # and/or upstream
git rebase origin/main
```

Adjust `origin/main` to `upstream/main` if that is your tracking main.

Resolve conflicts minimally; no opportunistic refactors.

```bash
git add <resolved files>
GIT_EDITOR=true git rebase --continue
```

## Step 3: Conflict Resolution Policy

Same as the original skill: minimal compatibility changes, keep feature intent,
resolve tests with production changes.

## Step 4: Verify No Code Loss

```bash
git range-diff origin/main...<approved-base> origin/main...HEAD
git diff --name-status <approved-base>..HEAD
git merge-base --is-ancestor origin/main HEAD
git log --left-right --cherry-pick --oneline origin/main...HEAD
```

## Step 5: Full Validation Pipeline

```bash
make test && make verify
```

If validation fails, treat as incorrect rebase; restore baseline and retry.

## Step 6: Final Report

- Rebase completed (yes/no)
- Branch and ahead/behind
- `make test` result
- `make verify` result
