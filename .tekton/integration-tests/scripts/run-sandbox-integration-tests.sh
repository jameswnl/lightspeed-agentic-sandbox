#!/usr/bin/env bash
# Run sandbox BDD integration tests for a single LLM provider.
#
# Called by the Konflux sandbox-integration-test-pipeline after
# cloning the repo at the SNAPSHOT revision.
#
# Usage:
#   bash .tekton/integration-tests/scripts/run-sandbox-integration-tests.sh <provider>
#
# Expects:
#   - Provider credentials mounted at /var/run/credentials/token
#   - ARTIFACT_DIR set (for junit XML output)
#   - Working directory is the sandbox repo root

set -euo pipefail

PROVIDER="${1:?Usage: $0 <provider> (claude|gemini|openai)}"
CRED_PATH="/var/run/credentials/token"

if [ ! -f "${CRED_PATH}" ]; then
    echo "error: credential file not found at ${CRED_PATH}" >&2
    exit 1
fi

# --- Set up provider credentials ---
case "${PROVIDER}" in
  claude)
    mkdir -p "${HOME}/.config/gcloud"
    cp "${CRED_PATH}" "${HOME}/.config/gcloud/application_default_credentials.json"
    export GOOGLE_APPLICATION_CREDENTIALS="${CRED_PATH}"
    export CLAUDE_CODE_USE_VERTEX=1
    ANTHROPIC_VERTEX_PROJECT_ID=$(python3 -c "import json; print(json.load(open('${CRED_PATH}'))['project_id'])")
    export ANTHROPIC_VERTEX_PROJECT_ID
    ;;
  gemini)
    mkdir -p "${HOME}/.config/gcloud"
    cp "${CRED_PATH}" "${HOME}/.config/gcloud/application_default_credentials.json"
    export GOOGLE_APPLICATION_CREDENTIALS="${CRED_PATH}"
    ;;
  openai)
    OPENAI_API_KEY=$(cat "${CRED_PATH}")
    export OPENAI_API_KEY
    ;;
  *)
    echo "error: unknown provider: ${PROVIDER}" >&2
    exit 1
    ;;
esac

# --- Install uv if not present ---
if ! command -v uv >/dev/null 2>&1; then
    pip install --quiet uv
fi

# --- Run BDD tests in host mode ---
ARTIFACT_DIR="${ARTIFACT_DIR:-/workspace/artifacts}"
mkdir -p "${ARTIFACT_DIR}"
export E2E_ARGS="--junitxml=${ARTIFACT_DIR}/junit_e2e.xml --tb=short"
E2E_PROW_HOST=1 bash scripts/e2e-containers.sh "${PROVIDER}"
