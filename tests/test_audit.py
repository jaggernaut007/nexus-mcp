"""Tests for audit logging middleware (Phase 5d)."""

import json
import logging

from nexus_mcp.middleware.audit import (
    AuditLogger,
    AuditRecord,
    _sanitize_params,
    generate_correlation_id,
)


def test_generate_correlation_id_length():
    """Correlation IDs are 12 hex chars."""
    cid = generate_correlation_id()
    assert len(cid) == 12
    assert all(c in "0123456789abcdef" for c in cid)


def test_generate_correlation_id_unique():
    """Correlation IDs are unique."""
    ids = {generate_correlation_id() for _ in range(100)}
    assert len(ids) == 100


def test_sanitize_params_redacts_sensitive():
    """Sensitive fields are redacted."""
    params = {"query": "test", "token": "secret123", "api_key": "key"}
    sanitized = _sanitize_params(params)
    assert sanitized["query"] == "test"
    assert sanitized["token"] == "[REDACTED]"
    assert sanitized["api_key"] == "[REDACTED]"


def test_sanitize_params_truncates_long_values():
    """Long string values are truncated."""
    params = {"query": "x" * 1000}
    sanitized = _sanitize_params(params)
    assert len(sanitized["query"]) < 600
    assert "[truncated]" in sanitized["query"]


def test_sanitize_params_preserves_short_values():
    """Short values are preserved as-is."""
    params = {"query": "test", "limit": 10}
    sanitized = _sanitize_params(params)
    assert sanitized == params


def test_audit_record_to_json():
    """AuditRecord serializes to valid JSON."""
    record = AuditRecord(
        timestamp="2026-03-12T10:00:00",
        correlation_id="abc123def456",
        tool_name="search",
        params_sanitized={"query": "test"},
        result_status="success",
        duration_ms=42.5,
    )
    data = json.loads(record.to_json())
    assert data["tool_name"] == "search"
    assert data["duration_ms"] == 42.5
    assert data["result_status"] == "success"


def test_audit_logger_logs_invocation(caplog):
    """AuditLogger emits structured log records."""
    audit = AuditLogger(enabled=True)
    with caplog.at_level(logging.INFO, logger="nexus.audit"):
        record = audit.log_invocation(
            tool_name="search",
            params={"query": "test"},
            result_status="success",
            duration_ms=15.3,
            correlation_id="abc123def456",
        )
    assert record is not None
    assert record.tool_name == "search"
    assert "search" in caplog.text


def test_audit_logger_disabled():
    """Disabled AuditLogger returns None and does not log."""
    audit = AuditLogger(enabled=False)
    record = audit.log_invocation(
        tool_name="index",
        params={"path": "/tmp"},
        result_status="success",
        duration_ms=100.0,
        correlation_id="abc123def456",
    )
    assert record is None


def test_audit_logger_sanitizes_before_logging(caplog):
    """AuditLogger sanitizes params before logging."""
    audit = AuditLogger(enabled=True)
    with caplog.at_level(logging.INFO, logger="nexus.audit"):
        record = audit.log_invocation(
            tool_name="search",
            params={"query": "test", "password": "secret123"},
            result_status="success",
            duration_ms=10.0,
            correlation_id="abc123def456",
        )
    assert record.params_sanitized["password"] == "[REDACTED]"
    assert "secret123" not in caplog.text


def test_audit_logger_records_error_status(caplog):
    """AuditLogger correctly records error status."""
    audit = AuditLogger(enabled=True)
    with caplog.at_level(logging.INFO, logger="nexus.audit"):
        record = audit.log_invocation(
            tool_name="index",
            params={"path": "/nonexistent"},
            result_status="error",
            duration_ms=5.0,
            correlation_id="abc123def456",
        )
    assert record.result_status == "error"
