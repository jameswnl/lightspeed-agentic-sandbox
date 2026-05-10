---
name: investigate-ci-failure
description: Investigate CI job failures on a GitHub pull request (checks, Prow, or GitHub Actions). Use when the user pastes a PR URL and asks about CI failures or red checks.
disable-model-invocation: true
---

# Investigate CI Failure

Default repo: **openshift/lightspeed-agentic-sandbox**. Adjust `org/repo` if the
user gives another URL.

## Workflow

### 1. Extract PR info

```bash
gh api repos/{org}/{repo}/pulls/{pr} --jq '{title, state, user: .user.login, head_sha: .head.sha}'
gh api repos/{org}/{repo}/pulls/{pr}/files --jq '.[].filename'
```

### 2. Get check statuses

```bash
gh pr checks {pr} --repo {org}/{repo}
gh api repos/{org}/{repo}/commits/{head_sha}/check-runs --jq '.check_runs[] | select(.conclusion == "failure") | {name, html_url}'
```

For OpenShift Prow, `gh pr checks` often surfaces Prow job links; use the
**target URL** from failed contexts to open logs.

### 3. Prow / GCS artifacts (when applicable)

From a Prow `target_url` like:

`https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/{org}_{repo}/{pr}/{job_name}/{build_id}`

Derive artifact bases as in the upstream skill (GCS browser + raw `storage.googleapis.com` URLs) and fetch the failing step log (build, unit test, lint).

### 4. Konflux / Tekton

This repo uses Konflux (`.tekton/`). If checks reference Tekton/Konflux:

- Open the failed pipeline run from the GitHub check details.
- Read the failing task log (often `build`, `prefetch-dependencies`, or EC).

### 5. Diagnose

- Classify: dependency prefetch vs lint vs test vs image build.
- Tie failure to a file or command in this repo (Makefile target, `pyproject.toml`, Containerfile).
- Suggest the smallest fix; do not guess — quote the log line.

### 6. Report

- Failed job name(s) + link(s)
- Root cause (one paragraph)
- Concrete next step (file/command)
