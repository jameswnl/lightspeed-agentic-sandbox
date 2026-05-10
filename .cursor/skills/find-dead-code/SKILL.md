---
name: find-dead-code
description: Find unused functions, classes, imports, and unreachable code paths. Use when the user asks for dead code or cleanup candidates.
disable-model-invocation: true
---

# Find Dead Code

Detect unused code that can be safely removed.

## Rules

- Report findings, do not delete unless the user asks.
- Focus on `src/lightspeed_agentic/`. Skip tests unless asked.
- Vulture has false positives — validate each finding.
- Code used via dynamic dispatch (FastAPI, Pydantic, provider plugins) may look unused.

## Step 1: Determine Scope

- **Branch mode**: changed files vs `main`.
- **Full mode**: all of `src/lightspeed_agentic/`.

Branch mode:

```bash
git diff --name-only origin/main -- 'src/lightspeed_agentic/' | grep '\.py$'
```

## Step 2: Run Vulture

```bash
uvx vulture <target> --min-confidence 80
```

## Step 3: Classify

For each hit: confirm with `rg` / imports; mark as true dead code, false
positive, or optional cleanup.

## Step 4: Report

Table: symbol, file:line, confidence, recommendation.
