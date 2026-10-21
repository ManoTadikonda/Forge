"""Domain errors.

Every error carries a stable ``code`` used by the API error envelope. Messages are
written for operators and must never embed secrets or stack traces.
"""

from __future__ import annotations


class ForgeError(Exception):
    """Base class for all domain errors."""

    code: str = "forge_error"
    http_status: int = 500

    def __init__(self, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ValidationError(ForgeError):
    code = "validation_error"
    http_status = 422


class NotFoundError(ForgeError):
    code = "not_found"
    http_status = 404


class ConflictError(ForgeError):
    code = "conflict"
    http_status = 409


class AuthenticationError(ForgeError):
    code = "authentication_required"
    http_status = 401


class AuthorizationError(ForgeError):
    code = "forbidden"
    http_status = 403


class RateLimitedError(ForgeError):
    code = "rate_limited"
    http_status = 429


class IllegalTransitionError(ConflictError):
    """Raised when a state machine is asked to make a transition that is not legal."""

    code = "illegal_transition"


class UnsafeUrlError(ValidationError):
    """Raised when a supplied repository URL fails egress/SSRF validation."""

    code = "unsafe_url"


class LockAcquisitionError(ConflictError):
    code = "lock_unavailable"


class FencedError(ConflictError):
    """Raised when a worker acts under a lease that has since been superseded."""

    code = "fenced"


class BuildError(ForgeError):
    code = "build_failed"
    http_status = 500


class DeploymentError(ForgeError):
    code = "deployment_failed"
    http_status = 500
