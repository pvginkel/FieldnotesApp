"""Testing service for test utilities like auth sessions."""

import logging
import secrets
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TestSession:
    """Represents a test authentication session."""

    subject: str
    name: str | None
    email: str | None
    roles: list[str]


class TestingService:
    """Service for testing utilities like auth sessions.

    Test session state is stored at the class level so it persists across
    Factory-created instances within the same process.
    """

    # Class-level storage for test sessions (token -> session data)
    _sessions: dict[str, TestSession] = {}

    # Forced error status for /api/auth/self (single-shot)
    _forced_auth_error: int | None = None

    # ── Test session management ──────────────────────────────────────

    def create_session(
        self,
        subject: str,
        name: str | None = None,
        email: str | None = None,
        roles: list[str] | None = None,
    ) -> str:
        """Create a test session and return a session token.

        Args:
            subject: User subject identifier
            name: User display name
            email: User email address
            roles: User roles (defaults to empty list)

        Returns:
            Session token to be stored in cookie
        """
        token = f"test-session-{secrets.token_urlsafe(16)}"
        session = TestSession(
            subject=subject,
            name=name,
            email=email,
            roles=roles or [],
        )
        TestingService._sessions[token] = session

        logger.info(
            "Created test session: subject=%s name=%s email=%s roles=%s",
            subject,
            name,
            email,
            roles,
        )

        return token

    def get_session(self, token: str) -> TestSession | None:
        """Get a test session by token.

        Args:
            token: Session token from cookie

        Returns:
            TestSession if found, None otherwise
        """
        return TestingService._sessions.get(token)

    def clear_session(self, token: str) -> bool:
        """Clear a test session.

        Args:
            token: Session token to clear

        Returns:
            True if session was cleared, False if not found
        """
        if token in TestingService._sessions:
            del TestingService._sessions[token]
            logger.info("Cleared test session")
            return True
        return False

    def clear_all_sessions(self) -> None:
        """Clear all test sessions (for test isolation)."""
        TestingService._sessions.clear()
        logger.debug("Cleared all test sessions")

    def set_forced_auth_error(self, status_code: int) -> None:
        """Set a forced error for the next /api/auth/self request.

        Args:
            status_code: HTTP status code to return
        """
        TestingService._forced_auth_error = status_code
        logger.info("Set forced auth error: status=%d", status_code)

    def consume_forced_auth_error(self) -> int | None:
        """Consume and return the forced auth error (single-shot).

        Returns:
            HTTP status code if set, None otherwise
        """
        error = TestingService._forced_auth_error
        TestingService._forced_auth_error = None
        if error:
            logger.info("Consumed forced auth error: status=%d", error)
        return error
