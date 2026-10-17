"""Development server entry point."""

import logging
import os
import threading

from paste.translogger import TransLogger  # type: ignore[import-untyped]
from waitress import serve

from app import create_app
from app.app import App
from app.config import Settings
from app.consts import DEFAULT_BACKEND_PORT
from app.utils.lifecycle_coordinator import LifecycleEvent
from app.utils.log_formatter import ExtraFieldFormatter, resolve_log_level

logger = logging.getLogger(__name__)


def _log_app_config(app: App) -> None:
    """Dump AppSettings, if it can redact itself.

    AppSettings is app-owned. One without dump_for_logging() predates the
    Sensitive marks, so its secrets would go to the log unmasked: skip it.
    """
    dump = getattr(app.container.app_config(), "dump_for_logging", None)
    if callable(dump):
        for key, value in dump().items():
            logger.info("app_config  %-40s = %s", key, value)


def main() -> None:
    settings = Settings.load()

    handler = logging.StreamHandler()
    handler.setFormatter(ExtraFieldFormatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    logging.basicConfig(level=resolve_log_level(settings.log_level), handlers=[handler])

    # What this process booted with, secrets shown as ***
    for key, value in settings.dump_for_logging().items():
        logger.info("settings    %-40s = %s", key, value)

    # Enable debug mode for development and testing environments
    debug_mode = settings.flask_env in ("development", "testing")

    app = create_app(settings)
    _log_app_config(app)

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", DEFAULT_BACKEND_PORT))

    # Get and initialize the lifecycle coordinator
    lifecycle_coordinator = app.container.lifecycle_coordinator()

    if debug_mode:
        app.logger.info("Running in debug mode")
        lifecycle_coordinator.initialize()

        def signal_shutdown(lifecycle_event: LifecycleEvent) -> None:
            if lifecycle_event == LifecycleEvent.AFTER_SHUTDOWN:
                # app.run() holds the main thread; sys.exit would not end it.
                os._exit(0)

        lifecycle_coordinator.register_lifecycle_notification(signal_shutdown)

        # No Werkzeug reloader: it runs the app twice, a parent that watches
        # files and a child that serves, and only the child would run the
        # background services and the lifecycle coordinator. One process here;
        # scripts/dev-watch.sh restarts it on a code change, through SIGTERM
        # and the lifecycle coordinator's graceful shutdown.
        app.run(host=host, port=port, debug=True, use_reloader=False)
    else:
        lifecycle_coordinator.initialize()

        def runner() -> None:
            # Production: Use Waitress WSGI server
            wsgi = TransLogger(app, setup_console_handler=False)

            # Thread count balances concurrency with DB connection pool size.
            # With pool_size=20 + max_overflow=30 = 50 connections available,
            # we match Waitress threads to avoid silent connection pool queuing.
            threads = int(os.getenv("WAITRESS_THREADS", 50))
            wsgi.logger.info(f"Using Waitress WSGI server with {threads} threads")
            serve(
                wsgi,
                host=host,
                port=port,
                threads=threads,
                trusted_proxy="*",
                trusted_proxy_count=1,
                trusted_proxy_headers="x-forwarded-for x-forwarded-proto x-forwarded-host",
            )

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()

        event = threading.Event()

        def signal_shutdown_prod(lifecycle_event: LifecycleEvent) -> None:
            if lifecycle_event == LifecycleEvent.AFTER_SHUTDOWN:
                event.set()

        lifecycle_coordinator.register_lifecycle_notification(signal_shutdown_prod)

        event.wait()

if __name__ == "__main__":
    main()
