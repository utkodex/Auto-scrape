from __future__ import annotations

import os

import pytest

from tests.fixtures.sf_like.server import start

# Tests must never run in live mode or try to reach a real LLM.
os.environ.setdefault("MODE", "build")
os.environ.setdefault("HEADLESS", "true")


@pytest.fixture(scope="session")
def fixture_server():
    """Serve the SuccessFactors-like fixture pages; yields the base URL (with trailing slash)."""
    url, stop = start()
    yield url
    stop()


@pytest.fixture(scope="session")
def browser(tmp_path_factory):
    """One headless persistent-context browser for the whole test session."""
    from core.browser import Browser

    b = Browser(
        site="fixture",
        allowed_hosts=["127.0.0.1"],
        headless=True,
        profiles_root=tmp_path_factory.mktemp("browser_profiles"),
    )
    yield b
    b.close()
