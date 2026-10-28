"""Authentication helpers."""

import hashlib
import hmac

from shop.errors import Unauthorized
from shop.models import Customer


def hash_password(password: str, salt: str) -> str:
    """Return a salted SHA-256 hex digest of the password."""
    return hashlib.sha256((salt + password).encode()).hexdigest()


def verify_token(token: str, secret: str) -> bool:
    """Constant-time check that a token matches the expected secret."""
    return hmac.compare_digest(token, secret)


def require_admin(customer: Customer) -> None:
    """Raise Unauthorized unless the customer is an admin."""
    if not customer.is_admin:
        raise Unauthorized("admin role required")
