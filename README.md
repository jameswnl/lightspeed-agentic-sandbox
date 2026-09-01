# lightspeed-agentic-sandbox

Multi-provider agentic sandbox library for OpenShift Lightspeed.

See [CLAUDE.md](CLAUDE.md) for architecture and usage, and [evals/](evals/README.md) for the evaluation framework.

Local development uses `uv`. Run `make install` for dev dependencies,
`make install-all` for all providers plus evals, and `make lock` to refresh
`uv.lock` after dependency changes.

## Bumping Dependencies

The container image is built hermetially in Konflux. After changing
dependencies in `pyproject.toml`, regenerate the lockfiles:

```bash
make bump-deps          # upgrade uv.lock + regenerate requirements.{arch}.txt
make rpm-lockfile       # regenerate rpms.lock.yaml (needs podman)
```

See [CLAUDE.md](CLAUDE.md#konflux-hermetic-builds) for full details on the
hermetic build setup and how to add new dependencies.

## Publishing the personal dev image (quay.io/jameswong/lightspeed-agentic-sandbox)

The Konflux pipeline above is the officially supported build path, but it
pushes to the internal Red Hat tenant registry. For local development and
testing against `lightspeed-cloud-agents`/`lightspeed-stack`, a personal dev
image is also published to `quay.io/jameswong/lightspeed-agentic-sandbox`.

**`:latest` must always be a multi-arch (amd64 + arm64) manifest list.**
Pushing a single-arch build directly as `:latest` silently breaks ephemeral
sandbox spawns on whichever architecture isn't included -- e.g. a
Mac-built arm64-only `:latest` works fine locally but makes every ephemeral
spawn hang indefinitely in "Provisioning" on an amd64 OpenShift cluster,
with no clear error (this happened once; see
lightspeed-cloud-agents issue #238's investigation history).

After pushing fresh `:latest-amd64` / `:latest-arm64` single-arch images,
always finish by re-assembling and publishing the combined manifest list:

```bash
scripts/publish-dev-manifest.sh
```

This script only assembles and pushes the manifest list from the two
existing per-arch tags (it does not build images), and verifies both
architectures are present both before and after pushing -- it refuses to
push if either architecture is missing, so `:latest` can't silently
regress to single-arch again.
