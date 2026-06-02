#!/usr/bin/env bash
# Run E2E BDD tests against one live sandbox container at a time.
#
# Usage (from lightspeed-agentic-sandbox/):
#   bash scripts/e2e-containers.sh                  # all three providers (sequential)
#   bash scripts/e2e-containers.sh openai          # one provider, default model from config.env
#   bash scripts/e2e-containers.sh openai gpt-4.1-nano   # optional model override
#
# OpenShift CI / no container runtime (host uvicorn):
#   E2E_PROW_HOST=1 bash scripts/e2e-containers.sh openai
#   bash scripts/e2e-containers.sh --prow-host openai
# Optional: E2E_SKIP_INSTALL=1 if deps already installed; E2E_HOST_PORT=8080 (default);
# ARTIFACT_DIR is used for E2E_OUTPUT_DIR when set (Prow).
#
# Exports SANDBOX_SERVICE_URL and E2E_PROVIDER for pytest. Missing credentials exit non-zero
# before any container starts.

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "${SCRIPT_DIR}/.." && pwd)
cd "${ROOT}"

E2E_PROW_HOST="${E2E_PROW_HOST:-0}"
if [[ "${1:-}" == "--prow-host" ]]; then
    E2E_PROW_HOST=1
    shift
fi

# IMAGE / runtime must be non-empty OCI references. Whitespace-only or stray CR (e.g. from env)
# makes podman fail with: Error: invalid reference format (exit 125).
_e2e_trim() {
    local s="${1:-}"
    s="${s//$'\r'/}"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "${s}"
}

RUNTIME="${CONTAINER_RUNTIME:-$(command -v podman 2>/dev/null || command -v docker 2>/dev/null)}"
RUNTIME="$(_e2e_trim "${RUNTIME}")"
UV="${UV:-uv}"
PORT="${E2E_PORT:-18080}"
CONFIG_ENV="${ROOT}/tests/e2e/config.env"

IMAGE="$(_e2e_trim "${IMAGE:-lightspeed-agentic-sandbox:latest}")"
if [[ -z "${IMAGE}" || -z "${IMAGE// }" ]]; then
    IMAGE="lightspeed-agentic-sandbox:latest"
fi

if [ ! -f "${CONFIG_ENV}" ]; then
    echo "e2e: missing ${CONFIG_ENV}" >&2
    exit 1
fi

if [[ "${E2E_PROW_HOST}" != "1" ]]; then
    if [[ -z "${RUNTIME}" ]] || ! command -v "${RUNTIME}" >/dev/null 2>&1; then
        echo "e2e: no container runtime (set CONTAINER_RUNTIME or install podman/docker)" >&2
        echo "e2e: or use E2E_PROW_HOST=1 / --prow-host for host uvicorn (e.g. OpenShift CI)" >&2
        exit 1
    fi
fi

NAME=""
SERVER_PID=""
E2E_SKILLS_WORKDIR=""

# Container-created files may be owned by mapped UIDs (rootless podman).
# Try plain rm first, fall back to runtime unshare.
_rm_container_owned() {
    local target="$1"
    [ -e "${target}" ] || return 0
    rm -rf "${target}" 2>/dev/null \
        || "${RUNTIME}" unshare rm -rf "${target}" 2>/dev/null \
        || true
}

cleanup() {
    if [[ -n "${SERVER_PID:-}" ]]; then
        kill "${SERVER_PID}" 2>/dev/null || true
        SERVER_PID=""
    fi
    if [ -n "${NAME}" ]; then
        "${RUNTIME}" logs "e2e-${NAME}" >"${ROOT}/.e2e-last-container.log" 2>&1 || true
        "${RUNTIME}" stop "e2e-${NAME}" 2>/dev/null || true
        "${RUNTIME}" rm -f "e2e-${NAME}" 2>/dev/null || true
        NAME=""
    fi
    _rm_container_owned "${ROOT}/.e2e"
    [ -n "${GCLOUD_TMP:-}" ] && rm -f "${GCLOUD_TMP}" 2>/dev/null || true
}
trap cleanup EXIT

provider_to_image_provider() {
    case "$1" in
        claude)
            if [ -n "${GCLOUD_MOUNT}" ]; then echo "vertex"; else echo "anthropic"; fi
            ;;
        gemini) echo "vertex" ;;
        *) echo "$1" ;;
    esac
}

provider_to_model_provider() {
    case "$1" in
        claude)
            if [ -n "${GCLOUD_MOUNT}" ]; then echo "anthropic"; else echo ""; fi
            ;;
        gemini) echo "google" ;;
        *) echo "" ;;
    esac
}

apply_model_override() {
    local provider="$1"
    local model="$2"
    case "${provider}" in
        claude) export ANTHROPIC_MODEL="${model}" ;;
        gemini) export GEMINI_MODEL="${model}" ;;
        openai) export OPENAI_MODEL="${model}" ;;
        *)
            echo "e2e: unknown provider for model override: ${provider}" >&2
            exit 1
            ;;
    esac
}

model_env_var() {
    local provider="$1"
    case "${provider}" in
        claude) printf '%s' "LIGHTSPEED_MODEL=${ANTHROPIC_MODEL:-}" ;;
        gemini) printf '%s' "LIGHTSPEED_MODEL=${GEMINI_MODEL:-}" ;;
        openai) printf '%s' "LIGHTSPEED_MODEL=${OPENAI_MODEL:-}" ;;
        *)
            echo "e2e: unknown provider: ${provider}" >&2
            exit 1
            ;;
    esac
}

prepare_e2e_skills_workspace() {
    local ws="${ROOT}/tests/e2e/workspace"
    E2E_SKILLS_WORKDIR="${ROOT}/.e2e/skills"
    _rm_container_owned "${E2E_SKILLS_WORKDIR}"
    mkdir -p "${E2E_SKILLS_WORKDIR}"
    for _skill_dir in "${ws}/skills"/*/; do
        [ -d "${_skill_dir}" ] && cp -a "${_skill_dir}" "${E2E_SKILLS_WORKDIR}/$(basename "${_skill_dir}")"
    done
    chmod -R a+rwX "${E2E_SKILLS_WORKDIR}"
}

LLM_CREDS_PATH="/var/run/secrets/llm-credentials"
GCLOUD_ADC="${HOME}/.config/gcloud/application_default_credentials.json"
GCLOUD_MOUNT=""
GCLOUD_TMP=""
if [ -f "${GCLOUD_ADC}" ]; then
    # Copy to a world-readable temp file so the container user (UID 1001) can read it.
    GCLOUD_TMP="$(mktemp /tmp/gcloud-adc-XXXXXX.json)"
    cp "${GCLOUD_ADC}" "${GCLOUD_TMP}"
    chmod 644 "${GCLOUD_TMP}"
    # Mount at the operator-expected credential path so config.py can find it.
    GCLOUD_MOUNT="-v ${GCLOUD_TMP}:${LLM_CREDS_PATH}/GOOGLE_APPLICATION_CREDENTIALS:ro,Z"
fi

# Derive LIGHTSPEED_* values from legacy env vars (CI secrets, gcloud config).
VERTEX_PROJECT="${ANTHROPIC_VERTEX_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
VERTEX_REGION="${CLOUD_ML_REGION:-${GOOGLE_CLOUD_LOCATION:-us-east5}}"

run_one_host() {
    local provider="$1"
    local model_override="${2:-}"
    local agent_provider
    local host_port="${E2E_HOST_PORT:-8080}"
    local outdir="${ROOT}/.e2e/output-${provider}"

    agent_provider=$(provider_to_image_provider "${provider}")

    # shellcheck disable=SC1090
    set -a && source <(sed 's/\r$//' "${CONFIG_ENV}") && set +a

    if [ -n "${model_override}" ]; then
        apply_model_override "${provider}" "${model_override}"
    fi

    if [[ -z "${E2E_SKIP_INSTALL:-}" ]]; then
        make install-all
    fi

    "${UV}" run --extra e2e python tests/e2e/credentials.py check "${provider}"

    rm -rf "${outdir}" 2>/dev/null || true
    mkdir -p "${outdir}"
    chmod -R a+rwX "${outdir}"

    prepare_e2e_skills_workspace
    export LIGHTSPEED_PROVIDER="${agent_provider}"
    local mp
    mp=$(provider_to_model_provider "${provider}")
    if [ -n "${mp}" ]; then
        export LIGHTSPEED_MODEL_PROVIDER="${mp}"
    fi
    # Translate SDK-specific model vars (from config.env) into LIGHTSPEED_MODEL.
    local model_pair
    model_pair=$(model_env_var "${provider}")
    export "${model_pair}"
    export LIGHTSPEED_PROVIDER_URL="${OPENAI_BASE_URL:-}"
    export LIGHTSPEED_PROVIDER_PROJECT="${VERTEX_PROJECT:-}"
    export LIGHTSPEED_PROVIDER_REGION="${VERTEX_REGION:-}"
    export LIGHTSPEED_SKILLS_DIR="${E2E_SKILLS_WORKDIR}"

    # Host mode: ensure credential file exists at the operator-expected path.
    if [ -f "${GCLOUD_ADC}" ]; then
        mkdir -p "${LLM_CREDS_PATH}"
        cp "${GCLOUD_ADC}" "${LLM_CREDS_PATH}/GOOGLE_APPLICATION_CREDENTIALS"
    fi

    echo "e2e: starting uvicorn (host) for ${provider} (image provider=${agent_provider}) on :${host_port}..."
    "${UV}" run python -m uvicorn lightspeed_agentic.app:app --host 0.0.0.0 --port "${host_port}" &
    SERVER_PID=$!

    echo "e2e: waiting for /health on port ${host_port}..."
    local attempt
    for attempt in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:${host_port}/health" >/dev/null 2>&1; then
            echo "e2e: ${provider} ready"
            break
        fi
        if [ "${attempt}" -eq 60 ]; then
            echo "e2e: timeout waiting for ${provider}" >&2
            exit 1
        fi
        sleep 1
    done

    export SANDBOX_SERVICE_URL="http://127.0.0.1:${host_port}"
    export E2E_PROVIDER="${provider}"
    export E2E_OUTPUT_DIR="${ARTIFACT_DIR:-$outdir}"

    echo "e2e: running pytest for ${provider}..."
    # shellcheck disable=SC2086
    "${UV}" run --extra e2e pytest -c tests/e2e/pytest.ini tests/e2e -v ${E2E_ARGS:-}
}

run_one() {
    local provider="$1"
    local model_override="${2:-}"
    NAME="${provider}"
    local agent_provider
    agent_provider=$(provider_to_image_provider "${provider}")

    # shellcheck disable=SC1090
    set -a && source <(sed 's/\r$//' "${CONFIG_ENV}") && set +a
    # config.env must never blank IMAGE; re-apply defaults after sourcing.
    IMAGE="$(_e2e_trim "${IMAGE:-lightspeed-agentic-sandbox:latest}")"
    if [[ -z "${IMAGE}" || -z "${IMAGE// }" ]]; then
        IMAGE="lightspeed-agentic-sandbox:latest"
    fi

    if [ -n "${model_override}" ]; then
        apply_model_override "${provider}" "${model_override}"
    fi

    "${UV}" run --extra e2e python tests/e2e/credentials.py check "${provider}"

    local outdir="${ROOT}/.e2e/output-${provider}"
    rm -rf "${outdir}" 2>/dev/null || true
    mkdir -p "${outdir}"
    chmod -R a+rwX "${outdir}"

    prepare_e2e_skills_workspace

    echo "e2e: starting container for ${provider} (image provider=${agent_provider})..."
    if [[ -z "${IMAGE// }" ]]; then
        echo "e2e: IMAGE is empty after normalization; fix your environment or Makefile" >&2
        exit 1
    fi
    # shellcheck disable=SC2086
    "${RUNTIME}" run -d --rm \
        --name "e2e-${provider}" \
        -p "${PORT}:8080" \
        -v "${E2E_SKILLS_WORKDIR}:/app/skills:Z" \
        -v "${outdir}:/app/e2e-output:Z" \
        -e PYTHONPATH="/app/src:/opt/app-root/lib64/python3.12/site-packages" \
        ${GCLOUD_MOUNT} \
        -e LIGHTSPEED_PROVIDER="${agent_provider}" \
        -e LIGHTSPEED_MODEL_PROVIDER="$(provider_to_model_provider "${provider}")" \
        -e "$(model_env_var "${provider}")" \
        -e LIGHTSPEED_PROVIDER_URL="${OPENAI_BASE_URL:-}" \
        -e LIGHTSPEED_PROVIDER_PROJECT="${VERTEX_PROJECT:-}" \
        -e LIGHTSPEED_PROVIDER_REGION="${VERTEX_REGION:-}" \
        -e LIGHTSPEED_SKILLS_DIR="/app/skills" \
        -e E2E_OUTPUT_DIR="/app/e2e-output" \
        -e ANTHROPIC_API_KEY \
        -e OPENAI_API_KEY \
        -e AWS_ACCESS_KEY_ID \
        -e AWS_SECRET_ACCESS_KEY \
        "${IMAGE}"

    echo "e2e: waiting for /health on port ${PORT}..."
    local attempt
    for attempt in $(seq 1 60); do
        if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
            echo "e2e: ${provider} ready"
            break
        fi
        if [ "${attempt}" -eq 60 ]; then
            echo "e2e: timeout waiting for ${provider}" >&2
            "${RUNTIME}" logs "e2e-${provider}" 2>&1 | tail -30 >&2 || true
            exit 1
        fi
        sleep 1
    done

    export SANDBOX_SERVICE_URL="http://127.0.0.1:${PORT}"
    export E2E_PROVIDER="${provider}"
    export E2E_OUTPUT_DIR="${outdir}"

    echo "e2e: running pytest for ${provider}..."
    # shellcheck disable=SC2086
    "${UV}" run --extra e2e pytest -c tests/e2e/pytest.ini tests/e2e -v ${E2E_ARGS:-}
}

PROVIDERS=(claude gemini openai)

if [[ "${E2E_PROW_HOST}" == "1" ]]; then
    if [[ $# -lt 1 ]]; then
        echo "e2e: --prow-host (or E2E_PROW_HOST=1) requires a provider: ${PROVIDERS[*]}" >&2
        exit 1
    fi
    provider="$1"
    shift || true
    model="${1:-}"
    if [ -n "${model}" ]; then
        shift || true
    fi
    run_one_host "${provider}" "${model}"
    exit 0
fi

if [ $# -eq 0 ]; then
    for p in "${PROVIDERS[@]}"; do
        NAME="${p}"
        cleanup || true
        NAME=""
        run_one "${p}" ""
        cleanup || true
        NAME=""
    done
else
    provider="$1"
    shift || true
    model="${1:-}"
    if [ -n "${model}" ]; then
        shift || true
    fi
    run_one "${provider}" "${model}"
fi
