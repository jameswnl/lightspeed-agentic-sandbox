# lightspeed-agentic-sandbox
#
# Multi-provider agent sandbox for OpenShift Lightspeed.
# Matches the production pod layout:
#   /skills      — all available skills, baked into the image at build time,
#                  read-only. Landlock-scoped per name by OpenShellSpawner
#                  (see allowed_skills in lightspeed-cloud-agents).
#   /app/skills  — empty at build time. Providers discover skills by
#                  *listing* this directory, and Landlock's allow-list model
#                  can't grant partial listing of /skills without granting
#                  full listing (which would defeat per-name scoping) --
#                  so OpenShellSpawner execs materialize-skills.sh
#                  (baked in below) before starting the agent server,
#                  copying just the allowed names from /skills into here.
#                  Must be group-writable (not just group-owned) for that
#                  copy to succeed regardless of which UID the compute
#                  driver actually runs the sandbox process as -- see
#                  lightspeed-cloud-agents issue #201 for the exact
#                  failure mode this avoids.
#   /tmp         — writable workspace for agent operations
#   /home/agent  — writable home directory
#
# Hermetic build: all dependencies are prefetched by Konflux/Hermeto.
# Network access is disabled during build.

# ---------------------------------------------------------------------------
# Builder stage: install Python deps from prefetched requirements
# ---------------------------------------------------------------------------
FROM registry.redhat.io/rhel9/python-312:latest AS builder

USER 0
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ src/
COPY requirements.*.txt ./

# Install Python packages from the platform-specific requirements file.
# In hermetic builds, Cachi2 sets PIP_* env vars pointing to prefetched deps.
# The unset avoids conflicts between Cachi2's --home and pip's --target.
RUN unset PIP_INSTALL_OPTIONS PIP_TARGET PIP_HOME PIP_PREFIX 2>/dev/null; \
    pip3.12 install --no-cache-dir --target /app/site-packages \
        -r requirements.$(uname -m).txt

# Install claude-code CLI.
# In hermetic builds (Konflux), cachi2 prefetches npm packages and sets the
# registry via cachi2.env — use npm ci against the lockfile.
# In non-hermetic builds (OpenShift BuildConfig), fall back to npm install -g.
COPY package.json package-lock.json ./
RUN dnf install -y --nodocs nodejs && dnf clean all
RUN if [ -f /cachi2/cachi2.env ]; then \
        . /cachi2/cachi2.env && \
        npm ci --ignore-scripts; \
    else \
        npm install -g @anthropic-ai/claude-code --ignore-scripts && \
        cp -a /usr/local/lib/node_modules /app/node_modules; \
    fi

# ---------------------------------------------------------------------------
# origincli stage: provides oc (kubectl is a symlink to oc in this image)
# ---------------------------------------------------------------------------
FROM registry.redhat.io/openshift4/ose-cli-rhel9:v4.21 AS origincli

# ---------------------------------------------------------------------------
# podman stage: provides catatonit init binary
# ---------------------------------------------------------------------------
FROM registry.redhat.io/ubi9/podman:9.8 AS podman

# ---------------------------------------------------------------------------
# Runtime stage: minimal image with only what the agent needs
# ---------------------------------------------------------------------------
FROM registry.redhat.io/rhel9/python-312-minimal:latest

USER 0
WORKDIR /app

# System packages (resolved from rpms.in.yaml via rpm prefetch).
# Split into functional groups for readability.

# Claude Code SDK requirements
RUN microdnf install -y --nodocs \
    bash git wget jq \
    && microdnf clean all

# SRE debugging toolkit
# tcpdump is installed separately with || true because it is not
# available in the UBI9 subset repos used by ci-operator builds (OpenShift CI).
# Konflux hermetic builds pre-fetch it via the RPM lockfile so the production
# image still includes tcpdump.
RUN microdnf install -y --nodocs \
    procps-ng iproute bind-utils net-tools openssl \
    lsof strace \
    less vim-minimal findutils file diffutils \
    skopeo unzip tar gzip \
    && (microdnf install -y --nodocs tcpdump || true) \
    && microdnf clean all

# Node.js runtime (for claude-code CLI)
RUN microdnf install -y --nodocs nodejs && microdnf clean all

# Copy Python site-packages from builder
COPY --from=builder /app/site-packages /opt/app-root/lib64/python3.12/site-packages

# Copy claude-code npm installation from builder
COPY --from=builder /app/node_modules /app/node_modules
RUN ln -s /app/node_modules/@anthropic-ai/claude-code/bin/claude.exe /usr/local/bin/claude

# oc from the origincli stage; kubectl is a symlink to oc in that image
COPY --from=origincli /usr/bin/oc /usr/bin/oc
RUN ln -s /usr/bin/oc /usr/bin/kubectl

# catatonit init binary from the podman stage
COPY --from=podman /usr/libexec/podman/catatonit /usr/bin/catatonit


# Copy application source outside /app so the agent workspace stays clean.
# The agent's Filesystem/Shell capabilities can see /app/; keeping source
# elsewhere prevents the LLM from reading sandbox internals during analysis.
# Intentionally root-owned and world-readable: the agent user should not modify app code.
COPY --from=builder /app/src /opt/lightspeed/src
COPY --from=builder /app/pyproject.toml /app/README.md /opt/lightspeed/
COPY LICENSE /licenses/LICENSE

# All available skills, baked in at /skills (outside /app -- see the header
# comment). Read-only content: nothing writes here at runtime, so plain
# root:root ownership with world-read/execute is sufficient; per-agent
# scoping happens via a Landlock read-only grant on specific
# /skills/<name> subdirectories, not by restricting what's baked in here.
COPY skills/ /skills/

# Copies the allowed_skills subset from /skills into /app/skills at
# spawn time -- see the header comment and the script's own comments.
COPY scripts/materialize-skills.sh /usr/local/bin/materialize-skills.sh
RUN chmod 0755 /usr/local/bin/materialize-skills.sh

# OpenShell supervisor expects a 'sandbox' user for privilege drop.
# Create as uid 1001 to match the existing non-root convention.
RUN useradd -m -u 1001 -d /home/agent sandbox 2>/dev/null || true && \
    mkdir -p /app/skills /tmp/agent-workspace /home/agent /sandbox && \
    chown -R 1001:0 /app /home/agent /tmp/agent-workspace /sandbox && \
    chmod -R g+w /app/skills && \
    chmod -R a+rX /skills

ENV SHELL="/bin/bash"
ENV HOME="/home/agent"
# Providers discover skills by listing this directory -- it must be the
# filtered /app/skills copy, not the read-only /skills master, or every
# baked-in skill loads regardless of allowed_skills. OpenShellSpawner
# also sets this explicitly on every spawn (belt-and-suspenders); this
# default matters for anything that runs the image without going
# through OpenShellSpawner's materialize-skills.sh step (e.g. this
# repo's own e2e-containers.sh, which already mounts its own test
# fixtures at /app/skills).
ENV LIGHTSPEED_SKILLS_DIR="/app/skills"
ENV PYTHONPATH="/opt/lightspeed/src:/opt/app-root/lib64/python3.12/site-packages"
ENV PATH="/usr/local/bin:${PATH}"

USER 1001:1001

EXPOSE 8080

CMD ["sleep", "infinity"]

LABEL name="openshift-lightspeed/lightspeed-agentic-sandbox-rhel9" \
      summary="Multi-provider agent sandbox for OpenShift Lightspeed" \
      description="Python agent with Claude, Gemini, and OpenAI provider support" \
      cpe="cpe:/a:redhat:openshift_lightspeed:1::el9" \
      com.redhat.component="openshift-lightspeed" \
      io.k8s.display-name="OpenShift Lightspeed Agentic Sandbox" \
      io.k8s.description="Python agent with Claude, Gemini, and OpenAI provider support" \
      io.openshift.tags="openshift-lightspeed,ols" \
      konflux.additional-tags="latest"
