"""Temporary file storage management for AI analysis features."""

import hashlib
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import NamedTuple
from uuid import uuid4

from app.utils.lifecycle_coordinator import LifecycleCoordinatorProtocol, LifecycleEvent

logger = logging.getLogger(__name__)


class CachedContent(NamedTuple):
    """Cached download content with metadata."""
    content: bytes
    content_type: str
    timestamp: datetime


class TempFileManager:
    """
    Manages temporary file storage with automatic cleanup.

    Creates timestamped directories for storing temporary files
    and runs a background cleanup thread to remove old files.
    """

    def __init__(
        self,
        lifecycle_coordinator: LifecycleCoordinatorProtocol,
        base_path: str = "/tmp/app-temp",
        cleanup_age_hours: float = 24.0,
    ):
        """
        Initialize the temporary file manager.

        Args:
            lifecycle_coordinator: Coordinator for lifecycle events
            base_path: Base directory for temporary file storage
            cleanup_age_hours: Age in hours after which files are cleaned up
        """
        self.base_path = Path(base_path)
        self.cleanup_age_hours = cleanup_age_hours
        self.lifecycle_coordinator = lifecycle_coordinator
        self._cleanup_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

        # Ensure base directory exists (also used for download cache)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self.cache_path = self.base_path

        # Register lifecycle notification
        self.lifecycle_coordinator.register_lifecycle_notification(self._on_lifecycle_event)

    def start_cleanup_thread(self) -> None:
        """Start the background cleanup thread."""
        if self._cleanup_thread is None or not self._cleanup_thread.is_alive():
            self._cleanup_thread = threading.Thread(
                target=self._cleanup_loop, daemon=True
            )
            self._cleanup_thread.start()
            logger.info("Started temporary file cleanup thread")

    def _stop_cleanup_thread(self) -> None:
        """Stop the background cleanup thread."""
        self._shutdown_event.set()
        if self._cleanup_thread and self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=5.0)
            logger.info("Stopped temporary file cleanup thread")

    def create_temp_directory(self) -> Path:
        """
        Create a new temporary directory with timestamp and UUID.

        Returns:
            Path to the created temporary directory

        Example:
            /tmp/20240830_143022_a1b2c3d4/
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        uuid_suffix = str(uuid4())[:8]
        dir_name = f"{timestamp}_{uuid_suffix}"

        temp_dir = self.base_path / dir_name
        temp_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"Created temporary directory: {temp_dir}")
        return temp_dir

    def cleanup_old_files(self) -> int:
        """
        Clean up temporary directories older than cleanup_age_hours.

        Returns:
            Number of directories cleaned up
        """
        if not self.base_path.exists():
            return 0

        cutoff_time = datetime.now() - timedelta(hours=self.cleanup_age_hours)
        cleaned_count = 0

        try:
            for item in self.base_path.iterdir():
                if not item.is_dir():
                    continue

                # Get directory creation time
                stat_info = item.stat()
                created_time = datetime.fromtimestamp(stat_info.st_ctime)

                if created_time < cutoff_time:
                    try:
                        # Remove directory and all contents
                        import shutil
                        shutil.rmtree(item)
                        cleaned_count += 1
                        logger.debug(f"Cleaned up old temporary directory: {item}")
                    except Exception as e:
                        logger.warning(f"Failed to clean up directory {item}: {e}")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}")

        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} old temporary directories")

        return cleaned_count

    def _cleanup_loop(self) -> None:
        """Background thread loop for periodic cleanup."""
        cleanup_interval = 3600  # Run every hour

        while not self._shutdown_event.wait(cleanup_interval):
            try:
                self.cleanup_old_files()
            except Exception as e:
                logger.error(f"Error in cleanup loop: {e}")

    def _url_to_path(self, url: str) -> str:
        """
        Convert URL to cache file path using SHA256 hash.

        Args:
            url: URL to convert

        Returns:
            SHA256 hash of the URL for use as filename
        """
        return hashlib.sha256(url.encode('utf-8')).hexdigest()

    def get_cached(self, url: str) -> CachedContent | None:
        """
        Retrieve cached content for a URL if it exists and is valid.

        Args:
            url: URL to look up in cache

        Returns:
            CachedContent if cached and valid, None otherwise
        """
        cache_key = self._url_to_path(url)
        content_file = self.cache_path / f"{cache_key}.bin"
        metadata_file = self.cache_path / f"{cache_key}.json"

        # Check if both files exist
        if not content_file.exists() or not metadata_file.exists():
            return None

        try:
            # Load metadata
            with open(metadata_file, encoding='utf-8') as f:
                metadata = json.load(f)

            cached_time = datetime.fromisoformat(metadata['timestamp'])

            # Check if cache is still valid
            if (datetime.now() - cached_time >
                timedelta(hours=self.cleanup_age_hours)):
                return None

            # Load content
            with open(content_file, 'rb') as f:
                content = f.read()

            return CachedContent(
                content=content,
                content_type=metadata['content_type'],
                timestamp=cached_time
            )

        except (OSError, json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to load cached content for {url}: {e}")
            return None

    def cache(self, url: str, content: bytes, content_type: str) -> bool:
        """
        Store content in cache for the given URL.

        Args:
            url: URL to cache content for
            content: Raw bytes of the content
            content_type: MIME type of the content

        Returns:
            True if caching succeeded, False otherwise
        """
        try:
            cache_key = self._url_to_path(url)
            content_file = self.cache_path / f"{cache_key}.bin"
            metadata_file = self.cache_path / f"{cache_key}.json"

            # Store content
            with open(content_file, 'wb') as f:
                f.write(content)

            # Store metadata
            metadata = {
                'url': url,
                'content_type': content_type,
                'timestamp': datetime.now().isoformat(),
                'size': len(content)
            }

            with open(metadata_file, 'w', encoding='utf-8') as f:
                json.dump(metadata, f, indent=2)

            logger.debug(f"Cached content for URL {url} ({len(content)} bytes)")
            return True

        except OSError as e:
            logger.error(f"Failed to cache content for {url}: {e}")
            return False

    def _on_lifecycle_event(self, event: LifecycleEvent) -> None:
        """Callback when a lifecycle event occurs."""
        match event:
            case LifecycleEvent.SHUTDOWN:
                self.shutdown()

    def shutdown(self) -> None:
        """Implementation of the shutdown sequence, also for use by unit tests."""
        self._stop_cleanup_thread()
