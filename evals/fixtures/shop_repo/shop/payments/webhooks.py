"""Incoming payment webhooks."""

import hashlib
import hmac


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Check the HMAC-SHA256 signature the gateway sends with each webhook."""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def handle_payment_webhook(body: bytes, signature: str, secret: str) -> str:
    """Return 'accepted' for a valid webhook and 'rejected' otherwise."""
    if not verify_signature(body, signature, secret):
        return "rejected"
    return "accepted"
