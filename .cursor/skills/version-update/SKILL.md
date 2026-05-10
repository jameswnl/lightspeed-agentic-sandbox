---
name: version-update
description: Bump the Python package version in pyproject.toml for a release or when the user asks to change the project version.
disable-model-invocation: true
---

# Version Update

**Source of truth:** `pyproject.toml` → `[project]` → `version = "X.Y.Z"`.

There is no separate `version.py` or OpenAPI artifact in this repo that must
stay in lockstep; bump the single `version` field unless the user points to
another file that encodes it (e.g. future release automation).

## Checklist

- [ ] `pyproject.toml` `[project].version` updated
- [ ] If release notes or tags are used, mention the same version there (user-directed)

## Notes

- Container image tags are typically driven by CI/Konflux from git revision, not
  this field alone.
- After bumping, run `make verify` and `make test` before tagging or releasing.
