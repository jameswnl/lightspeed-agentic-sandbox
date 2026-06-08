UV := uv

CONTAINER_RUNTIME := $(shell command -v podman 2>/dev/null || command -v docker 2>/dev/null)
IMAGE := lightspeed-agentic-sandbox:latest

ifneq ($(filter e2e,$(MAKECMDGOALS)),)
E2E_EXTRA_TARGETS := $(filter-out e2e,$(MAKECMDGOALS))
ifneq ($(E2E_EXTRA_TARGETS),)
.PHONY: $(E2E_EXTRA_TARGETS)
$(E2E_EXTRA_TARGETS):
	@:
endif
endif

.PHONY: install install-all lock test lint format mypy verify eval eval-report e2e image clean help \
       requirements bump-deps rpm-lockfile

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Install package with dev dependencies via uv
	$(UV) sync --extra dev

install-all: ## Install all provider, dev, and eval dependencies via uv
	$(UV) sync --all-extras

lock: ## Refresh uv.lock from pyproject.toml
	$(UV) lock

test: ## Run unit tests
	$(UV) run pytest tests/ -v --ignore=tests/e2e

lint: ## Run ruff linter
	$(UV) run ruff check .

format: ## Auto-format with ruff
	$(UV) run ruff format .
	$(UV) run ruff check . --fix

mypy: ## Run mypy against application package
	$(UV) run mypy src/lightspeed_agentic

verify: ## Run non-mutating formatting, lint, and type checks
	$(UV) run ruff format . --check
	$(UV) run ruff check .
	$(UV) run mypy src/lightspeed_agentic

image: ## Build container image for local development and evals
	$(CONTAINER_RUNTIME) build -t $(IMAGE) .

EVAL_ARGS ?=

eval: image ## Run evals against live containers (use EVAL_ARGS to filter, e.g. EVAL_ARGS="-k claude")
	PYTEST="$(UV) run pytest" bash evals/run.sh $(EVAL_ARGS)

eval-report: image ## Run evals and generate JSON report
	PYTEST="$(UV) run pytest" bash evals/run.sh --eval-report=evals/report.json $(EVAL_ARGS)

e2e: image ## E2E BDD (make e2e | make e2e openai | make e2e openai gpt-4.1-mini). Optional: E2E_ARGS for pytest.
	IMAGE="$(IMAGE)" E2E_ARGS="$(E2E_ARGS)" bash scripts/e2e-containers.sh $(filter-out e2e,$(MAKECMDGOALS))

requirements: pyproject.toml ## Generate requirements.txt files for Konflux hermetic builds
	$(UV) pip compile pyproject.toml --extra all --extra eval \
		-o requirements.x86_64.txt --generate-hashes \
		--python-platform x86_64-unknown-linux-gnu --upgrade
	$(UV) pip compile pyproject.toml --extra all --extra eval \
		-o requirements.aarch64.txt --generate-hashes \
		--python-platform aarch64-unknown-linux-gnu --upgrade

bump-deps: ## Upgrade all dependencies and regenerate requirements
	$(UV) lock --upgrade
	$(MAKE) requirements

rpm-lockfile: rpms.in.yaml ubi.repo ## Regenerate rpms.lock.yaml (requires podman + Fedora)
	$(CONTAINER_RUNTIME) run --rm \
		-v "$$(pwd):/workdir:z" -w /workdir \
		-v "$${HOME}/.docker/config.json:/root/.docker/config.json:ro" \
		registry.fedoraproject.org/fedora:latest bash -c " \
		dnf install -y -q python3 python3-pip python3-dnf skopeo rpm && \
		python3 -m pip install -q --break-system-packages \
			https://github.com/konflux-ci/rpm-lockfile-prototype/archive/refs/heads/main.zip && \
		rpm-lockfile-prototype --image registry.redhat.io/rhel9/python-312-minimal:latest rpms.in.yaml"

clean: ## Remove build artifacts and caches
	rm -rf dist/ build/ *.egg-info .venv .pytest_cache .mypy_cache .ruff_cache node_modules/
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
