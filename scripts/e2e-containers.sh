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
# Skill token output uses a host tmpdir (E2E_OUTPUT_DIR); removed after pytest.
# When ARTIFACT_DIR is set (Prow), outputs are copied there before cleanup;
# pytest stdout is tee'd to e2e-<provider>-pytest.log with a short summary file.
# On macOS, gcloud ADC is copied to .e2e/llm-credentials when /var/run/secrets is not writable.
#
# Exports SANDBOX_SERVICE_URL and E2E_PROVIDER for pytest. Missing credentials exit non-zero
# before any container starts.

set -euo pipefail
trap 'echo "error: $0 line $LINENO: command \"$BASH_COMMAND\" exited with status $?" >&2' ERR

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

RUNTIME="${CONTAINER_RUNTIME:-$(command -v podman 2>/dev/null || command -v docker 2>/dev/null || true)}"
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
E2E_HOST_PORT_ACTIVE=""

# Container-created files may be owned by mapped UIDs (rootless podman).
# Try plain rm first, fall back to runtime unshare.
_rm_container_owned() {
    local target="$1"
    [ -e "${target}" ] || return 0
    rm -rf "${target}" 2>/dev/null \
        || "${RUNTIME}" unshare rm -rf "${target}" 2>/dev/null \
        || true
}

APP_SOURCE_MARKER="/opt/lightspeed/src/lightspeed_agentic/app.py"

_ensure_container_runtime() {
    if "${RUNTIME}" info >/dev/null 2>&1; then
        return 0
    fi
    if [[ "$(basename "${RUNTIME}")" == "podman" ]] && podman machine list --format '{{.Name}}' 2>/dev/null | grep -q .; then
        echo "e2e: podman machine not running — starting it..." >&2
        podman machine start >/dev/null 2>&1 || true
    fi
    if "${RUNTIME}" info >/dev/null 2>&1; then
        return 0
    fi
    echo "e2e: cannot connect to ${RUNTIME}" >&2
    if [[ "$(basename "${RUNTIME}")" == "podman" ]]; then
        echo "e2e: try: podman machine start" >&2
    fi
    exit 1
}

_verify_image_app_source() {
    local image="$1"
    _ensure_container_runtime
    if ! "${RUNTIME}" image inspect "${image}" >/dev/null 2>&1; then
        echo "e2e: pulling ${image}..." >&2
        if ! "${RUNTIME}" pull "${image}" >/dev/null; then
            echo "e2e: image ${image} not found — build it first, e.g.: make image" >&2
            exit 1
        fi
    fi
    local size
    size="$("${RUNTIME}" run --rm "${image}" wc -c "${APP_SOURCE_MARKER}" 2>/dev/null | awk '{print $1}')" || true
    if [[ -z "${size}" || "${size}" -eq 0 ]]; then
        echo "e2e: image ${image} has empty or missing ${APP_SOURCE_MARKER}" >&2
        echo "e2e: stale build cache can cause this — rebuild without cache, e.g.:" >&2
        echo "e2e:   ${RUNTIME} build --no-cache -t ${image} -f Containerfile ." >&2
        exit 1
    fi
}

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

# Load model defaults from tests/e2e/config.env without host SDK model vars leaking in
# (e.g. OPENAI_MODEL=claude-opus-4-6 from a Claude dev shell breaks OpenAI e2e).
source_e2e_config() {
    local anthropic gemini openai
    read -r anthropic gemini openai < <(
        env -i bash -c "set -a; source \"${CONFIG_ENV}\"; set +a; printf '%s %s %s\n' \"\$ANTHROPIC_MODEL\" \"\$GEMINI_MODEL\" \"\$OPENAI_MODEL\""
    )
    export ANTHROPIC_MODEL="${anthropic}"
    export GEMINI_MODEL="${gemini}"
    export OPENAI_MODEL="${openai}"
}

_assert_host_port_available() {
    local port="$1"
    if ! command -v lsof >/dev/null 2>&1; then
        return 0
    fi
    local pids
    pids="$(lsof -tiTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
    if [ -n "${pids}" ]; then
        echo "e2e: port ${port} already in use (listener pids: ${pids})" >&2
        echo "e2e: stop the existing process or set E2E_HOST_PORT to a free port" >&2
        return 1
    fi
}

_stop_host_uvicorn() {
    local port="$1"
    if [[ -n "${SERVER_PID:-}" ]]; then
        pkill -P "${SERVER_PID}" 2>/dev/null || true
        kill "${SERVER_PID}" 2>/dev/null || true
        wait "${SERVER_PID}" 2>/dev/null || true
        SERVER_PID=""
    fi
}

cleanup() {
    if [[ -n "${E2E_HOST_PORT_ACTIVE:-}" ]]; then
        _stop_host_uvicorn "${E2E_HOST_PORT_ACTIVE}"
        E2E_HOST_PORT_ACTIVE=""
    elif [[ -n "${SERVER_PID:-}" ]]; then
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

# Keep LIGHTSPEED_MODEL and the provider SDK model var aligned for the server process.
sync_provider_model() {
    local provider="$1"
    unset LIGHTSPEED_MODEL
    case "${provider}" in
        claude)
            export LIGHTSPEED_MODEL="${ANTHROPIC_MODEL:-}"
            export ANTHROPIC_MODEL="${LIGHTSPEED_MODEL}"
            ;;
        gemini)
            export LIGHTSPEED_MODEL="${GEMINI_MODEL:-}"
            export GEMINI_MODEL="${LIGHTSPEED_MODEL}"
            ;;
        openai)
            export LIGHTSPEED_MODEL="${OPENAI_MODEL:-}"
            export OPENAI_MODEL="${LIGHTSPEED_MODEL}"
            ;;
        *)
            echo "e2e: unknown provider: ${provider}" >&2
            exit 1
            ;;
    esac
}

provider_sdk_model_env() {
    local provider="$1"
    case "${provider}" in
        claude) printf '%s' "ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-}" ;;
        gemini) printf '%s' "GEMINI_MODEL=${GEMINI_MODEL:-}" ;;
        openai) printf '%s' "OPENAI_MODEL=${OPENAI_MODEL:-}" ;;
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

prepare_host_llm_creds() {
    if [ ! -f "${GCLOUD_ADC}" ]; then
        return 0
    fi
    local dest_dir="${LLM_CREDS_PATH}"
    if ! mkdir -p "${dest_dir}" 2>/dev/null; then
        dest_dir="${ROOT}/.e2e/llm-credentials"
        mkdir -p "${dest_dir}"
        export LIGHTSPEED_LLM_CREDENTIALS_PATH="${dest_dir}"
        echo "e2e: using ${dest_dir} for LLM credentials (cannot write ${LLM_CREDS_PATH})" >&2
    fi
    cp "${GCLOUD_ADC}" "${dest_dir}/GOOGLE_APPLICATION_CREDENTIALS"
    chmod 600 "${dest_dir}/GOOGLE_APPLICATION_CREDENTIALS"
    export GOOGLE_APPLICATION_CREDENTIALS="${dest_dir}/GOOGLE_APPLICATION_CREDENTIALS"
}

LLM_CREDS_PATH="/var/run/secrets/llm-credentials"
GCLOUD_ADC="${HOME}/.config/gcloud/application_default_credentials.json"
GCLOUD_MOUNT=""
GCLOUD_TMP=""
if [ -f "${GCLOUD_ADC}" ]; then
    # Copy to a world-readable temp file so the container user (UID 1001) can read it.
    # Template must end with XXXXXX (GNU + BSD mktemp); avoid a suffix after the X's.
    mkdir -p "${ROOT}/.e2e"
    GCLOUD_TMP="$(mktemp "${ROOT}/.e2e/gcloud-adc.XXXXXX")"
    cp "${GCLOUD_ADC}" "${GCLOUD_TMP}"
    chmod 644 "${GCLOUD_TMP}"
    # Mount at the operator-expected credential path so config.py can find it.
    GCLOUD_MOUNT="-v ${GCLOUD_TMP}:${LLM_CREDS_PATH}/GOOGLE_APPLICATION_CREDENTIALS:ro,Z"
fi

# Derive LIGHTSPEED_* values from legacy env vars (CI secrets, gcloud config).
VERTEX_PROJECT="${ANTHROPIC_VERTEX_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
VERTEX_REGION="${CLOUD_ML_REGION:-${GOOGLE_CLOUD_LOCATION:-us-east5}}"

_e2e_tmp_output_dir() {
    local provider="$1"
    local tmp_root="${TMPDIR:-/tmp}"
    tmp_root="${tmp_root%/}"
    mktemp -d "${tmp_root}/lightspeed-e2e-${provider}.XXXXXX"
}

_prepare_e2e_output_dir() {
    local dir="$1"
    mkdir -p "${dir}"
    chmod -R a+rwX "${dir}"
}

_sync_artifact_dir() {
    local src="$1"
    if [ -n "${ARTIFACT_DIR:-}" ] && [ "${src}" != "${ARTIFACT_DIR}" ]; then
        mkdir -p "${ARTIFACT_DIR}"
        cp -a "${src}/." "${ARTIFACT_DIR}/" 2>/dev/null || true
    fi
}

_cleanup_e2e_output_dir() {
    local dir="${1:-}"
    if [ -z "${dir}" ] || [ ! -d "${dir}" ]; then
        return 0
    fi
    _sync_artifact_dir "${dir}"
    rm -rf "${dir}"
}

_run_e2e_pytest() {
    local provider="$1"
    echo "e2e: running pytest for ${provider}..."
    local pytest_exit=0
    if [ -n "${ARTIFACT_DIR:-}" ]; then
        mkdir -p "${ARTIFACT_DIR}"
        local log_file="${ARTIFACT_DIR}/e2e-${provider}-pytest.log"
        # shellcheck disable=SC2086
        set +e
        "${UV}" run --extra e2e pytest -c tests/e2e/pytest.ini tests/e2e -v ${E2E_ARGS:-} 2>&1 | tee "${log_file}"
        pytest_exit=${PIPESTATUS[0]}
        set -e
        cat >"${ARTIFACT_DIR}/e2e-${provider}-summary.txt" <<EOF
provider: ${provider}
LIGHTSPEED_MODEL: ${LIGHTSPEED_MODEL:-}
OPENAI_MODEL: ${OPENAI_MODEL:-}
ANTHROPIC_MODEL: ${ANTHROPIC_MODEL:-}
GEMINI_MODEL: ${GEMINI_MODEL:-}
pytest exit: ${pytest_exit}
pytest log: e2e-${provider}-pytest.log
EOF
    else
        # shellcheck disable=SC2086
        set +e
        "${UV}" run --extra e2e pytest -c tests/e2e/pytest.ini tests/e2e -v ${E2E_ARGS:-}
        pytest_exit=$?
        set -e
    fi
    return "${pytest_exit}"
}

run_one_host() {
    local provider="$1"
    local model_override="${2:-}"
    local agent_provider
    local host_port="${E2E_HOST_PORT:-8080}"
    local e2e_tmp_dir

    agent_provider=$(provider_to_image_provider "${provider}")

    source_e2e_config

    if [ -n "${model_override}" ]; then
        apply_model_override "${provider}" "${model_override}"
    fi

    if [[ -z "${E2E_SKIP_INSTALL:-}" ]]; then
        make install-all
    fi

    prepare_host_llm_creds

    "${UV}" run --extra e2e python tests/e2e/credentials.py check "${provider}"

    prepare_e2e_skills_workspace
    e2e_tmp_dir="$(_e2e_tmp_output_dir "${provider}")"
    trap '_cleanup_e2e_output_dir "${e2e_tmp_dir:-}"' RETURN
    _prepare_e2e_output_dir "${e2e_tmp_dir}"
    export LIGHTSPEED_PROVIDER="${agent_provider}"
    local mp
    mp=$(provider_to_model_provider "${provider}")
    if [ -n "${mp}" ]; then
        export LIGHTSPEED_MODEL_PROVIDER="${mp}"
    fi
    sync_provider_model "${provider}"
    export LIGHTSPEED_PROVIDER_URL="${OPENAI_BASE_URL:-}"
    export LIGHTSPEED_PROVIDER_PROJECT="${VERTEX_PROJECT:-}"
    export LIGHTSPEED_PROVIDER_REGION="${VERTEX_REGION:-}"
    export LIGHTSPEED_SKILLS_DIR="${E2E_SKILLS_WORKDIR}"
    export E2E_OUTPUT_DIR="${e2e_tmp_dir}"

    _assert_host_port_available "${host_port}" || exit 1
    E2E_HOST_PORT_ACTIVE="${host_port}"
    echo "e2e: model LIGHTSPEED_MODEL=${LIGHTSPEED_MODEL:-} OPENAI_MODEL=${OPENAI_MODEL:-} ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-}"
    echo "e2e: E2E_OUTPUT_DIR=${E2E_OUTPUT_DIR}"
    echo "e2e: starting uvicorn (host) for ${provider} (image provider=${agent_provider}) on :${host_port}..."
    "${UV}" run python -m uvicorn lightspeed_agentic.app:app --host 0.0.0.0 --port "${host_port}" &
    SERVER_PID=$!
    sleep 0.5
    if ! kill -0 "${SERVER_PID}" 2>/dev/null; then
        echo "e2e: uvicorn failed to start on port ${host_port} (is another server still bound?)" >&2
        exit 1
    fi

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

    _run_e2e_pytest "${provider}"
    local pytest_exit=$?
    trap - RETURN
    _cleanup_e2e_output_dir "${e2e_tmp_dir}"
    _stop_host_uvicorn "${host_port}"
    E2E_HOST_PORT_ACTIVE=""
    return "${pytest_exit}"
}

run_one() {
    local provider="$1"
    local model_override="${2:-}"
    NAME="${provider}"
    local agent_provider
    agent_provider=$(provider_to_image_provider "${provider}")

    source_e2e_config
    # config.env must never blank IMAGE; re-apply defaults after sourcing.
    IMAGE="$(_e2e_trim "${IMAGE:-lightspeed-agentic-sandbox:latest}")"
    if [[ -z "${IMAGE}" || -z "${IMAGE// }" ]]; then
        IMAGE="lightspeed-agentic-sandbox:latest"
    fi

    if [ -n "${model_override}" ]; then
        apply_model_override "${provider}" "${model_override}"
    fi

    "${UV}" run --extra e2e python tests/e2e/credentials.py check "${provider}"

    local e2e_tmp_dir
    e2e_tmp_dir="$(_e2e_tmp_output_dir "${provider}")"
    trap '_cleanup_e2e_output_dir "${e2e_tmp_dir:-}"' RETURN
    _prepare_e2e_output_dir "${e2e_tmp_dir}"

    prepare_e2e_skills_workspace

    sync_provider_model "${provider}"
    echo "e2e: model LIGHTSPEED_MODEL=${LIGHTSPEED_MODEL:-} OPENAI_MODEL=${OPENAI_MODEL:-} ANTHROPIC_MODEL=${ANTHROPIC_MODEL:-}"

    _verify_image_app_source "${IMAGE}"

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
        -v "${e2e_tmp_dir}:/tmp/lightspeed-e2e-output:Z" \
        -e PYTHONPATH="/opt/lightspeed/src:/opt/app-root/lib64/python3.12/site-packages" \
        ${GCLOUD_MOUNT} \
        -e LIGHTSPEED_PROVIDER="${agent_provider}" \
        -e LIGHTSPEED_MODEL_PROVIDER="$(provider_to_model_provider "${provider}")" \
        -e "$(model_env_var "${provider}")" \
        -e "$(provider_sdk_model_env "${provider}")" \
        -e LIGHTSPEED_PROVIDER_URL="${OPENAI_BASE_URL:-}" \
        -e LIGHTSPEED_PROVIDER_PROJECT="${VERTEX_PROJECT:-}" \
        -e LIGHTSPEED_PROVIDER_REGION="${VERTEX_REGION:-}" \
        -e LIGHTSPEED_SKILLS_DIR="/app/skills" \
        -e E2E_OUTPUT_DIR="/tmp/lightspeed-e2e-output" \
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
    export E2E_OUTPUT_DIR="${e2e_tmp_dir}"

    _run_e2e_pytest "${provider}"
    local pytest_exit=$?
    trap - RETURN
    _cleanup_e2e_output_dir "${e2e_tmp_dir}"
    "${RUNTIME}" stop "e2e-${provider}" >/dev/null 2>&1 || true
    return "${pytest_exit}"
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
    exit $?
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
