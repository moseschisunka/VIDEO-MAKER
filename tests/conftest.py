"""Repository-wide pytest policy for production-readiness gates.

The default test run is deterministic and offline. Tests that can contact a live
provider must opt in explicitly with ``--run-live-provider``; HyperFrames tests
remain separately opt-in because they may download a CLI/browser and consume
substantial time or resources.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-live-provider",
        action="store_true",
        default=False,
        help="run tests marked live_provider (may contact external services)",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Apply release-gate markers and fail-closed live-test defaults.

    The contracts directory is the release-blocking contract surface. Marking it
    during collection keeps the marker authoritative without requiring every
    individual contract module to repeat a module-level declaration.
    """

    skip_live = pytest.mark.skip(
        reason="live_provider tests are opt-in; pass --run-live-provider explicitly"
    )
    for item in items:
        path = Path(str(item.fspath)).resolve()
        if "tests" in path.parts and "contracts" in path.parts:
            item.add_marker("release_blocker")
        if "live_provider" in item.keywords and not config.getoption("--run-live-provider"):
            item.add_marker(skip_live)
