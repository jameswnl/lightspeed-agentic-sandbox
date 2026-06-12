#!/usr/bin/env bash
set -euo pipefail

TOKEN=$(head -c 16 /dev/urandom | od -An -tx1 | tr -d ' \n')
OUTDIR="${E2E_OUTPUT_DIR:-${TMPDIR:-/tmp}/lightspeed-e2e-output}"

mkdir -p "${OUTDIR}"
printf '%s' "${TOKEN}" > "${OUTDIR}/.e2e_token"

printf '{"token": "%s", "status": "ok"}\n' "${TOKEN}"
