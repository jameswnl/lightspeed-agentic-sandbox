---
name: find-complexity
description: Find functions with high cyclomatic complexity, length, or parameter count. Use when the user asks for complexity hotspots or refactor candidates.
disable-model-invocation: true
---

# Find Complexity Hotspots

Identify functions that are hard to review, test, and maintain.

## Rules

- Report findings, do not refactor unless asked.
- Focus on `src/lightspeed_agentic/`. Skip tests unless asked.
- Rank by severity.

## Step 1: Determine Scope

- **Branch mode**: changed Python files vs `main`.
- **Full mode**: `src/lightspeed_agentic/`.

Branch mode:

```bash
git diff --name-only origin/main -- 'src/lightspeed_agentic/' | grep '\.py$'
```

## Step 2: Cyclomatic Complexity

```bash
uvx radon cc <target> -s -n C -a
```

Grade C or worse (typically complexity 11+).

## Step 3: Maintainability Index (optional)

```bash
uvx radon mi <target> -s
```

## Step 4: Report

List: function, file:line, complexity grade, brief note (nesting, branching,
mixed concerns). Prioritize provider adapters and route handlers if scores are high.
