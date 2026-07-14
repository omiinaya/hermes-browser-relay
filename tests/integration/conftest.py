"""
Integration test configuration.

Adds a pytest marker for integration tests that can be excluded
from the default test run with `-m "not integration"`.
"""

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: marks end-to-end tests that require a real browser (Chromium). "
        "Skipped unless --run-integration is passed.",
    )


def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration/E2E tests (require Chromium)",
    )
