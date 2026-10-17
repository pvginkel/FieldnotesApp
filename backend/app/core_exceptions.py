"""Exception classes the template owns.

``app/exceptions.py`` is app-owned: generated once and never updated. A class
that template code handles, but that an existing app's ``exceptions.py`` may
lack, lives here instead, so a template update never imports a name the app
does not have. Import these from this module.
"""

from app.exceptions import BusinessLogicException


class ExternalServiceException(BusinessLogicException):
    """Raised when a call to an external service fails; answered with 502.

    Args:
        operation: What the app was doing, phrased to follow "Cannot"
            ("fetch the device configuration").
        cause: What went wrong ("Keycloak answered 503").
    """

    def __init__(self, operation: str, cause: str) -> None:
        self.operation = operation
        self.cause = cause
        message = f"Cannot {operation} because external service failed: {cause}"
        super().__init__(message, error_code="EXTERNAL_SERVICE_ERROR")
