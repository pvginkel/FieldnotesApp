"""Pytest configuration and fixtures.

Infrastructure fixtures (app, client, session, OIDC, SSE) are defined in
conftest_infrastructure.py. This file re-exports them and adds app-specific
domain fixtures.
"""

# Import all infrastructure fixtures
from tests.conftest_infrastructure import *  # noqa: F403
