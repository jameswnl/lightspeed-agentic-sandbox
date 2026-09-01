#!/usr/bin/env bash
# Assemble and push a multi-arch :latest manifest list for the personal dev
# image at quay.io/jameswong/lightspeed-agentic-sandbox, from whatever
# :latest-amd64 / :latest-arm64 single-arch tags are currently pushed there.
#
# Why this script exists: the Konflux hermetic pipeline (.tekton/) is the
# officially supported build path and always produces proper multi-arch
# images, but it pushes to the internal Red Hat tenant registry, not this
# personal quay.io namespace. Whoever refreshes the personal :latest-amd64/
# :latest-arm64 dev tags has in the past pushed a fresh single-arch build
# directly as :latest instead of re-assembling the manifest list from both
# architectures -- silently turning :latest into a single-arch (arm64) image.
# That went undetected until it caused ephemeral sandboxes to hang
# indefinitely in "Provisioning" on an amd64 OpenShift cluster (which can't
# schedule/pull an arm64-only image), while working fine locally on an
# arm64 Mac. See lightspeed-cloud-agents issue #238's investigation history.
#
# Usage:
#   scripts/publish-dev-manifest.sh
#
# Prerequisites: podman logged in to quay.io (`podman login quay.io`), and
# both :latest-amd64 and :latest-arm64 already pushed (this script does not
# build images, only assembles and publishes the manifest list).

set -euo pipefail

IMAGE="quay.io/jameswong/lightspeed-agentic-sandbox"
MANIFEST_LIST="${IMAGE}:latest"
TMP_MANIFEST="${IMAGE}:latest-manifest-tmp-$$"

cleanup() {
  podman manifest rm "$TMP_MANIFEST" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "== Assembling multi-arch manifest for ${MANIFEST_LIST} =="
podman manifest create "$TMP_MANIFEST"
podman manifest add "$TMP_MANIFEST" "docker://${IMAGE}:latest-amd64"
podman manifest add "$TMP_MANIFEST" "docker://${IMAGE}:latest-arm64"

echo
echo "== Verifying both architectures are present before pushing =="
platforms=$(podman manifest inspect "$TMP_MANIFEST" | python3 -c '
import json, sys
data = json.load(sys.stdin)
platforms = sorted(m["platform"]["architecture"] for m in data["manifests"])
print(",".join(platforms))
')
if [[ "$platforms" != "amd64,arm64" ]]; then
  echo "ERROR: expected manifest platforms 'amd64,arm64', got '${platforms}'." >&2
  echo "Refusing to push -- this would silently repeat the single-arch :latest bug." >&2
  exit 1
fi
echo "OK: platforms = ${platforms}"

echo
echo "== Pushing ${MANIFEST_LIST} =="
podman manifest push --all "$TMP_MANIFEST" "docker://${MANIFEST_LIST}"

echo
echo "== Verifying the pushed :latest tag is a multi-arch manifest list =="
remote_platforms=$(curl -sf "https://quay.io/v2/jameswong/lightspeed-agentic-sandbox/manifests/latest" \
  -H "Accept: application/vnd.oci.image.index.v1+json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
platforms = sorted(m["platform"]["architecture"] for m in data.get("manifests", []))
print(",".join(platforms))
')
if [[ "$remote_platforms" != "amd64,arm64" ]]; then
  echo "ERROR: after push, quay.io reports platforms '${remote_platforms}', expected 'amd64,arm64'." >&2
  echo "The push may not have landed as a manifest list -- check quay.io manually." >&2
  exit 1
fi
echo "OK: ${MANIFEST_LIST} is multi-arch (${remote_platforms})."
