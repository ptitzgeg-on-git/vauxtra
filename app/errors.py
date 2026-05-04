"""Unified error handling for Vauxtra."""

import logging
from enum import Enum
from typing import Optional
from fastapi import HTTPException, status

_logger = logging.getLogger(__name__)


class ErrorCode(str, Enum):
    """Standardized error codes for API responses."""
    
    # Authentication & Authorization
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    
    # Validation
    INVALID_INPUT = "INVALID_INPUT"
    INVALID_DOMAIN = "INVALID_DOMAIN"
    INVALID_IP = "INVALID_IP"
    INVALID_PORT = "INVALID_PORT"
    
    # Resource management
    NOT_FOUND = "NOT_FOUND"
    ALREADY_EXISTS = "ALREADY_EXISTS"
    CONFLICT = "CONFLICT"
    
    # Provider operations
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    PROVIDER_AUTH_FAILED = "PROVIDER_AUTH_FAILED"
    PROVIDER_OPERATION_FAILED = "PROVIDER_OPERATION_FAILED"
    
    # System
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class VauxtraException(Exception):
    """Base exception for Vauxtra."""
    
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        http_status: int = status.HTTP_400_BAD_REQUEST,
        detail: Optional[str] = None,
        error_id: Optional[str] = None,
    ):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.detail = detail
        self.error_id = error_id
        super().__init__(message)
    
    def to_http_exception(self) -> HTTPException:
        """Convert to FastAPI HTTPException."""
        return HTTPException(
            status_code=self.http_status,
            detail={
                "code": self.code.value,
                "message": self.message,
                "detail": self.detail,
                "error_id": self.error_id,
            },
        )


class ProviderException(VauxtraException):
    """Exception from provider operations."""
    
    def __init__(
        self,
        provider_type: str,
        operation: str,
        error: str,
        detail: Optional[str] = None,
    ):
        message = f"{provider_type}: {operation} failed — {error}"
        super().__init__(
            code=ErrorCode.PROVIDER_OPERATION_FAILED,
            message=message,
            http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=detail,
        )
        self.provider_type = provider_type
        self.operation = operation


class ValidationError(VauxtraException):
    """Validation error for input data."""
    
    def __init__(self, field: str, reason: str):
        message = f"Invalid {field}: {reason}"
        super().__init__(
            code=ErrorCode.INVALID_INPUT,
            message=message,
            http_status=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=reason,
        )
        self.field = field


def safe_provider_call(
    provider_type: str,
    operation: str,
    fn,
    *args,
    default_on_error=None,
    log_error: bool = True,
    **kwargs,
):
    """
    Safely call a provider operation with consistent error handling.
    
    Args:
        provider_type: Provider name (e.g., "adguard")
        operation: Operation name (e.g., "test_connection")
        fn: Callable to invoke
        *args: Positional arguments
        default_on_error: Value to return on error (None logs and raises)
        log_error: Whether to log the error
        **kwargs: Keyword arguments
    
    Returns:
        Result of fn() or default_on_error on error
    
    Raises:
        ProviderException: If default_on_error is None
    """
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        error_msg = str(e)
        if log_error:
            _logger.error(
                f"Provider error: {provider_type}.{operation}()",
                exc_info=True,
                extra={
                    "provider": provider_type,
                    "operation": operation,
                    "error": error_msg,
                },
            )
        
        if default_on_error is not None:
            return default_on_error
        
        raise ProviderException(
            provider_type=provider_type,
            operation=operation,
            error=error_msg,
        )
