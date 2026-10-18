"""Pytest configuration and fixtures.

Infrastructure fixtures (app, client, session, OIDC, SSE) are defined in
conftest_infrastructure.py. This file re-exports them and adds app-specific
domain fixtures.

A fixture defined here after the import replaces the template's fixture of
the same name. To change the settings the test apps are built from, replace
``test_settings`` or ``test_app_settings``. With a database they are
session-scoped, because the app is, and a replacement keeps that scope.

A process the suite starts builds its app from the environment and ``.env``
alone, not from these fixtures (``tests/test_openapi_cache.py`` builds one
for the OpenAPI document). A setting the app cannot start without, which
only a fixture here gives, gets a default for those processes at the top of
this file, after the imports: ``os.environ.setdefault("MY_SETTING", "value")``.
Without one the suite passes where ``.env`` sets it, and fails in CI.
"""

# Import all infrastructure fixtures
from tests.conftest_infrastructure import *  # noqa: F403
