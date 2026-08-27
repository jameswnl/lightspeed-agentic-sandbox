#!/bin/bash
# Copy the allowed subset of baked-in skills into the directory agent
# providers actually discover skills from.
#
# All available skills are baked into this image at /skills/<name>
# (read-only, Landlock-scoped per name by OpenShellSpawner -- see
# lightspeed-cloud-agents' allowed_skills handling). Agent providers
# discover skills by *listing* LIGHTSPEED_SKILLS_DIR (iterdir()/
# list_skills_in_dir()), and Landlock's allow-list model can't restrict
# what a directory listing enumerates without also making that listing
# fail entirely -- a rule on /skills/<name> grants access under that
# path, not ReadDir on /skills itself.
#
# So instead of trying to make partial directory listing "just work"
# under Landlock, this script physically copies only the requested
# names into a plain, freshly-listable directory (/app/skills). The
# copy itself still reads through the same per-name Landlock grant on
# /skills/<name> -- an unlisted name isn't just absent from the copy,
# it's genuinely unreadable if this script or anything else tried to
# reach it directly.
#
# Invoked by OpenShellSpawner via exec_stream() with each allowed skill
# name as a separate argv element (never a shell string), so this
# script must do the same: iterate "$@", never re-interpolate a name
# into a nested shell invocation.
#
# Usage: materialize-skills.sh <skill-name> [<skill-name> ...]

set -euo pipefail

DEST="/app/skills"

for name in "$@"; do
    cp -r "/skills/${name}" "${DEST}/${name}"
done
