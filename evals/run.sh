#!/bin/bash
# Run evals against live container servers — one per provider.
# Usage: bash evals/run.sh [pytest args...]
#
# Starts a container per provider, waits for /health, runs evals, then tears down.
# Providers without credentials are automatically skipped by pytest.

set -euo pipefail

IMAGE="${IMAGE:-lightspeed-agentic-sandbox:latest}"
RUNTIME="${CONTAINER_RUNTIME:-$(command -v podman 2>/dev/null || command -v docker 2>/dev/null)}"
BASE_PORT=18080
EVAL_ARGS=("$@")

LLM_CREDS_PATH="/var/run/secrets/llm-credentials"
GCLOUD_ADC="$HOME/.config/gcloud/application_default_credentials.json"
GCLOUD_MOUNT=""
if [ -f "$GCLOUD_ADC" ]; then
    GCLOUD_MOUNT="-v $GCLOUD_ADC:${LLM_CREDS_PATH}/GOOGLE_APPLICATION_CREDENTIALS:ro,Z"
fi

VERTEX_PROJECT="${ANTHROPIC_VERTEX_PROJECT_ID:-${GOOGLE_CLOUD_PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}}"
VERTEX_REGION="${CLOUD_ML_REGION:-${GOOGLE_CLOUD_LOCATION:-us-east5}}"

PROVIDERS=("claude" "gemini" "openai")
CONTAINERS=()
WORKDIRS=()
OUTDIRS=()

cleanup() {
    for i in "${!PROVIDERS[@]}"; do
        name="${PROVIDERS[$i]}"
        outdir="$(pwd)/.eval-workspaces/output-${name}"
        $RUNTIME logs "eval-${name}" > "${outdir}/container.log" 2>&1 || true
        $RUNTIME stop "eval-${name}" 2>/dev/null || true
        $RUNTIME rm -f "eval-${name}" 2>/dev/null || true
    done
    for d in "${WORKDIRS[@]}"; do
        rm -rf "$d" 2>/dev/null || true
    done
}
trap cleanup EXIT

model_env() {
    case "$1" in
        claude) echo "-e LIGHTSPEED_MODEL=${ANTHROPIC_MODEL:-claude-sonnet-4-6}" ;;
        gemini) echo "-e LIGHTSPEED_MODEL=${GEMINI_MODEL:-gemini-3.1-pro-preview}" ;;
        openai) echo "-e LIGHTSPEED_MODEL=${OPENAI_MODEL:-gpt-5.4}" ;;
    esac
}

echo "Starting provider containers..."

mkdir -p "$(pwd)/.eval-workspaces"

for i in "${!PROVIDERS[@]}"; do
    name="${PROVIDERS[$i]}"
    port=$((BASE_PORT + i))
    if [ -n "${GCLOUD_MOUNT}" ] && [[ "$name" == "claude" ]]; then
        agent_provider="vertex"; model_provider="anthropic"
    else
        case "$name" in
            claude) agent_provider="anthropic"; model_provider="" ;;
            gemini) agent_provider="vertex"; model_provider="google" ;;
            *) agent_provider="$name"; model_provider="" ;;
        esac
    fi
    workdir=$(mktemp -d "$(pwd)/.eval-workspaces/eval-${name}-XXXXXX")
    outdir="$(pwd)/.eval-workspaces/output-${name}"
    mkdir -p "$outdir"
    WORKDIRS+=("$workdir")
    OUTDIRS+=("$outdir")
    cp -r "$(pwd)/evals/workspace/tools" "$workdir/tools"
    for skill in "$(pwd)/evals/workspace/skills"/*/; do
        [ -d "$skill" ] && cp -r "$skill" "$workdir/$(basename "$skill")"
    done
    chmod -R 777 "$workdir" "$outdir"

    cid=$($RUNTIME run -d --rm \
        --name "eval-${name}" \
        -p "${port}:8080" \
        -v "${workdir}:/app/workspace:Z" \
        -v "${outdir}:/app/eval-output:Z" \
        -e EVAL_OUTPUT_DIR="/app/eval-output" \
        -e PYTHONPATH="/opt/lightspeed/src:/opt/app-root/lib64/python3.12/site-packages" \
        $GCLOUD_MOUNT \
        -e LIGHTSPEED_PROVIDER="$agent_provider" \
        -e LIGHTSPEED_MODEL_PROVIDER="$model_provider" \
        -e LIGHTSPEED_PROVIDER_URL="${OPENAI_BASE_URL:-}" \
        -e LIGHTSPEED_PROVIDER_PROJECT="${VERTEX_PROJECT:-}" \
        -e LIGHTSPEED_PROVIDER_REGION="${VERTEX_REGION:-}" \
        -e LIGHTSPEED_SKILLS_DIR="/app/workspace" \
        -e ANTHROPIC_API_KEY \
        -e OPENAI_API_KEY \
        -e AWS_ACCESS_KEY_ID \
        -e AWS_SECRET_ACCESS_KEY \
        $(model_env "$name") \
        "$IMAGE")

    CONTAINERS+=("$cid")
    echo "  ${name}: port ${port} (container ${cid:0:12})"
done

# Wait for all servers to be healthy (parallel)
echo "Waiting for servers..."
WAIT_PIDS=()
for i in "${!PROVIDERS[@]}"; do
    name="${PROVIDERS[$i]}"
    port=$((BASE_PORT + i))
    (
        for attempt in $(seq 1 30); do
            if curl -sf "http://localhost:${port}/health" > /dev/null 2>&1; then
                echo "  ${name}: ready"
                exit 0
            fi
            sleep 1
        done
        echo "  ${name}: FAILED to start (timeout)"
        $RUNTIME logs "eval-${name}" 2>&1 | tail -10
        exit 1
    ) &
    WAIT_PIDS+=($!)
done
for pid in "${WAIT_PIDS[@]}"; do
    wait "$pid" || exit 1
done

# Build server URL + workdir maps as env vars for pytest
SERVER_URLS=""
WORKSPACE_MAP=""
for i in "${!PROVIDERS[@]}"; do
    name="${PROVIDERS[$i]}"
    port=$((BASE_PORT + i))
    SERVER_URLS="${SERVER_URLS}${name}=http://localhost:${port},"
    WORKSPACE_MAP="${WORKSPACE_MAP}${name}=${OUTDIRS[$i]},"
done

echo ""
echo "Running evals..."
PYTEST="${PYTEST:-python3 -m pytest}"

export EVAL_SERVER_URLS="$SERVER_URLS"
export EVAL_WORKSPACES="$WORKSPACE_MAP"
$PYTEST evals/ -v "${EVAL_ARGS[@]}"
