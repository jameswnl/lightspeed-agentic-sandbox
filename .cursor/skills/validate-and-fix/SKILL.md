---
name: validate-and-fix
description: Run install sync, unit tests, and make verify; auto-fix trivial failures (ruff format/fix, obvious test expectation updates). Use when the user asks to validate, run tests, check the pipeline, or verify changes are clean.
disable-model-invocation: true
---

# Validate & Auto-Fix

Run the project validation pipeline, auto-fix trivial issues, and re-run until
green or a real failure is found.

## Rules

- Never modify production logic to fix a test. Only fix test expectations,
  imports, formatting, and lint.
- Never skip or delete a failing test.
- Stop after 3 auto-fix cycles to avoid loops.
- Report real failures clearly; do not attempt speculative fixes.

## Step 0: Ensure Dependencies Are Installed

```bash
make install-all
```

Use `make install` if the user only needs dev tooling without all provider
extras. For most agent code paths, `install-all` matches CI-like coverage.

## Step 1: Run Unit Tests

```bash
make test
```

If all pass, proceed to Step 3.

If failures occur, classify each failure (see Step 2).

## Step 2: Classify and Fix Failures

**Auto-fixable** (fix immediately, then re-run Step 1):

| Type | Fix |
|------|-----|
| Missing `await` on async call | Add `await`, mark test `async def`, add `@pytest.mark.asyncio` |
| Test asserts old default value | Update assertion to match new default |
| Ruff format | `make format` |

**Real failures** (do not auto-fix without user):

- Logic errors in production code
- Assertion failures reflecting unclear behavior changes
- Import errors from missing optional extras (install with `make install-all`)

## Step 3: Run Verify

```bash
make verify
```

This runs: `ruff format --check`, `ruff check`, `mypy` on `src/lightspeed_agentic`.

If failures occur:

| Type | Fix |
|------|-----|
| Format | `make format` |
| Unused import (F401) | Remove the import |
| Other ruff-fixable | `make format` (includes `ruff check --fix`) |

Re-run `make verify` after each fix.

## Step 4: Report

Report exactly:

- `make test`: pass/fail (counts if useful)
- `make verify`: pass/fail
- Auto-fixes applied (file + what changed)
- Cycles used: N/3

Do not include unrelated diagnostics.
