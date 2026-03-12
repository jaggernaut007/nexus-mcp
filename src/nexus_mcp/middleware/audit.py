"""Audit logging middleware for Nexus-MCP.

Logs every tool invocation with structured metadata for observability.
"""

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Set

logger = logging.getLogger("nexus.audit")

# Fields that should be redacted in audit logs
SENSITIVE_FIELDS: Set[str] = {"token", "password", "secret", "api_key", "credential"}


def generate_correlation_id() -> str:
    """Generate a short correlation ID for request tracing."""
    return uuid.uuid4().hex[:12]


@dataclass
class AuditRecord:
    """Structured record of a tool invocation."""

    timestamp: str
    correlation_id: str
    tool_name: str
    params_sanitized: Dict[str, Any]
    result_status: str  # "success" or "error"
    duration_ms: float
    client_id: Optional[str] = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


def _sanitize_params(params: Dict[str, Any]) -> Dict[str, Any]:
    """Redact sensitive fields from parameters before logging."""
    sanitized = {}
    for key, value in params.items():
        if any(sensitive in key.lower() for sensitive in SENSITIVE_FIELDS):
            sanitized[key] = "[REDACTED]"
        elif isinstance(value, str) and len(value) > 500:
            sanitized[key] = value[:500] + "...[truncated]"
        else:
            sanitized[key] = value
    return sanitized


class AuditLogger:
    """Logs tool invocations with structured metadata."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def log_invocation(
        self,
        tool_name: str,
        params: Dict[str, Any],
        result_status: str,
        duration_ms: float,
        correlation_id: str,
        client_id: Optional[str] = None,
    ) -> Optional[AuditRecord]:
        """Log a tool invocation as a structured audit record."""
        if not self.enabled:
            return None

        record = AuditRecord(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            correlation_id=correlation_id,
            tool_name=tool_name,
            params_sanitized=_sanitize_params(params),
            result_status=result_status,
            duration_ms=round(duration_ms, 2),
            client_id=client_id,
        )

        logger.info(record.to_json())
        return record
