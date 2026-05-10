---
name: resolve-cve
description: >-
  Triage a CVE: assess impact against this repo's dependencies, then bump,
  document not-affected, or plan a code fix. Use when the user mentions a CVE,
  Jira vulnerability issue, or security advisory for a dependency.
---

# resolve-cve

Use this skill for **lightspeed-agentic-sandbox**. Workflow matches the
Lightspeed Service skill, but Jira filters and verification targets are adjusted.

## Finding issues (Jira)

If the user gives a Jira key or URL, use that issue directly.

If they ask what to triage, search your org's vulnerability backlog. **Do not**
assume the same `summary ~` filter as `lightspeed-service-api-rhel9`. For this
Konflux component, issues may reference:

- Component name: `lightspeed-agentic-sandbox` (see `.tekton/*.yaml`)
- Repository: `openshift/lightspeed-agentic-sandbox`

Build a JQL filter with your project admin's pattern, e.g.:

```
project = <PROJECT> AND type = Vulnerability AND statusCategory = "To Do"
  AND (summary ~ "lightspeed-agentic-sandbox" OR summary ~ "openshift/lightspeed-agentic-sandbox")
ORDER BY priority DESC
```

If no project access, ask the user for the Jira key or paste the flaw text.

## Step 1: Read the issue

Parse CVE ID, affected package, version range, and fix reference from the
issue body (same approach as the upstream skill).

## Step 2: Assess impact

1. Search `pyproject.toml` and `uv.lock` for the package (case-insensitive).
2. Compare installed version to the advisory range.
3. Check whether vulnerable APIs are used in `src/lightspeed_agentic/`.
4. Trace transitive deps in `uv.lock` if needed.

## Step 3: Present assessment

Use the same verdict template as the upstream skill (**NOT AFFECTED** /
**bump** / **code change**). **Stop for user acknowledgment** before resolving.

## Step 4: Resolve

- **Not affected:** Jira comment + transition per team policy (same structure
  as upstream skill).
- **Dependency bump:** follow **deps-update** with `uv lock --upgrade-package
  {package}`, then `make requirements`, `make verify`, `make test`.
- **Code fix:** rare; minimal change + tests; run `make verify && make test`.

Commit message examples:

```
fix: resolve CVE-YYYY-NNNNN — bump {package} to {version}
```

## Constraints

- User acknowledgment before acting on the verdict.
- Prefer targeted `--upgrade-package` over full `--upgrade` for CVE PRs.
- Run `make verify` and `make test` before declaring done.
