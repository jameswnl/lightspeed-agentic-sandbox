---
name: find-duplication
description: Find code duplication in the codebase. Supports branch-scoped or full sweep. Use when the user asks to find duplicated code or repeated patterns before a PR.
disable-model-invocation: true
---

# Find Code Duplication

Detect duplicated or near-duplicate code and suggest consolidation candidates.

## Rules

- Report findings, do not refactor unless the user asks.
- Focus on production code under `src/lightspeed_agentic/`. Skip test duplication unless asked.
- Group by severity: exact duplicates first, then near-duplicates.

## Step 1: Determine Scope

- **Branch mode**: files changed vs `main`.
- **Full mode**: entire `src/lightspeed_agentic/`.

Branch mode:

```bash
git diff --name-only origin/main -- 'src/lightspeed_agentic/' | grep '\.py$'
```

Full mode target: `src/lightspeed_agentic/`.

## Step 2: Run Pylint Duplicate Detection

```bash
uv run pylint --disable=all --enable=duplicate-code --min-similarity-lines=6 <target>
```

Filter false positives: import blocks, Pydantic boilerplate, one-liners.

## Step 3: Semantic Duplication Search

Look for similar signatures, repeated error handling, copy-pasted blocks:

```bash
rg "<distinctive pattern>" src/lightspeed_agentic/ --type py -l
```

## Step 4: Classify Findings

| Category | Action |
|----------|--------|
| Extract — identical logic in 3+ places | Shared helper |
| Parameterize — same structure, different values | Common function |
| Acceptable — different domains | Note only |
| Test-only | Fixtures (if user asked) |

## Step 5: Report

Files, line ranges, description, classification, suggested helper location.
Summary: counts and rough savings.
