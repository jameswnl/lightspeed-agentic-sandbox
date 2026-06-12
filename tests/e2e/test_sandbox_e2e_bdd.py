"""pytest-bdd glue for sandbox E2E scenarios (probes, run error handling, context prefix)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pytest_bdd import scenarios

pytestmark = pytest.mark.e2e

_FEATURES = Path(__file__).parent / "features"
scenarios((_FEATURES / "sandbox_e2e.feature").as_posix())
