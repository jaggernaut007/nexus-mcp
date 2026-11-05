"""Tests for the commercial licence key check (src/nexus_mcp/licensing.py)."""

import json
import socket
import stat
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from nexus_mcp import core_api, licensing
from nexus_mcp.evaluate import cli

KEY = "NEXUS-1C285B2D-6CE6-4BC7-B8BE-ADB6A7E304DA"
ACTIVATE = "/v1/customer-portal/license-keys/activate"
VALIDATE = "/v1/customer-portal/license-keys/validate"
DEACTIVATE = "/v1/customer-portal/license-keys/deactivate"


def _license_body(status="granted"):
    return {
        "id": "lk-1",
        "key": KEY,
        "display_key": "****-E304DA",
        "status": status,
        "expires_at": "2027-01-01T00:00:00Z",
    }


class _Stub(BaseHTTPRequestHandler):
    """A stand-in for api.polar.sh. `routes` maps a path to (status, body)."""

    routes: dict = {}
    calls: list = []

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        _Stub.calls.append((self.path, payload))
        code, body = _Stub.routes.get(self.path, (404, {}))
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # keep test output quiet
        pass


@pytest.fixture
def polar(tmp_path, monkeypatch):
    """A local Polar stub, a private licence folder, and a configured organization."""
    _Stub.routes = {
        ACTIVATE: (200, {"id": "act-1", "license_key": _license_body()}),
        VALIDATE: (200, _license_body()),
        DEACTIVATE: (204, {}),
    }
    _Stub.calls = []
    server = HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("NEXUS_LICENSE_API", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("NEXUS_LICENSE_ORG", "org-test")
    monkeypatch.setenv("NEXUS_LICENSE_HOME", str(tmp_path / "lic"))
    monkeypatch.delenv("NEXUS_LICENSE_KEY", raising=False)
    monkeypatch.delenv("NEXUS_OFFLINE", raising=False)
    licensing.reset_state()
    yield _Stub
    server.shutdown()
    server.server_close()
    licensing.reset_state()


def _paths(stub):
    return [path for path, _ in stub.calls]


def test_validate_without_a_key_makes_no_network_call(polar, monkeypatch):
    def boom(*args, **kwargs):
        raise AssertionError("a network connection was opened")

    monkeypatch.setattr(socket.socket, "connect", boom)
    assert licensing.validate().status == licensing.NONE
    assert licensing.current_state().status == licensing.NONE
    assert licensing.start_background_validation() is False
    assert polar.calls == []


def test_activate_granted_gives_active_and_saves_the_key_owner_only(polar):
    state = licensing.activate(KEY)
    assert state.status == licensing.ACTIVE
    record = json.loads(licensing.license_file().read_text())
    assert record["key"] == KEY
    assert record["activation_id"] == "act-1"
    assert stat.S_IMODE(licensing.license_file().stat().st_mode) == 0o600


def test_activate_sends_only_key_org_and_a_random_label(polar):
    licensing.activate(KEY)
    path, payload = polar.calls[0]
    assert path == ACTIVATE
    assert set(payload) == {"key", "organization_id", "label"}
    assert payload["organization_id"] == "org-test"
    assert payload["label"].startswith("nexus-mcp-ci-")
    assert socket.gethostname() not in payload["label"]


def test_activate_unknown_key_gives_invalid_and_saves_nothing(polar):
    polar.routes[ACTIVATE] = (404, {})
    state = licensing.activate(KEY)
    assert state.status == licensing.INVALID
    assert not licensing.license_file().exists()


def test_activate_over_the_limit_names_the_fix(polar):
    polar.routes[ACTIVATE] = (403, {})
    state = licensing.activate(KEY)
    assert state.status == licensing.INVALID
    assert "activation limit" in state.message


def test_validate_env_key_activates_once_then_validates(polar, monkeypatch):
    monkeypatch.setenv("NEXUS_LICENSE_KEY", KEY)
    assert licensing.validate().status == licensing.ACTIVE
    assert _paths(polar) == [ACTIVATE]
    assert licensing.validate(force=True).status == licensing.ACTIVE
    assert _paths(polar) == [ACTIVATE, VALIDATE]
    assert polar.calls[1][1]["activation_id"] == "act-1"


def test_validate_env_key_is_not_written_to_disk(polar, monkeypatch):
    monkeypatch.setenv("NEXUS_LICENSE_KEY", KEY)
    licensing.validate()
    assert KEY not in licensing.license_file().read_text()


def test_validate_revoked_key_gives_revoked(polar):
    licensing.activate(KEY)
    polar.routes[VALIDATE] = (200, _license_body("revoked"))
    state = licensing.validate(force=True)
    assert state.status == licensing.REVOKED


def test_validate_rejected_key_gives_invalid(polar):
    licensing.activate(KEY)
    polar.routes[VALIDATE] = (404, {})
    assert licensing.validate(force=True).status == licensing.INVALID


def test_validate_uses_the_cache_for_a_day(polar):
    licensing.activate(KEY)
    polar.calls.clear()
    assert licensing.validate().status == licensing.ACTIVE
    assert polar.calls == []


def _age_last_check(seconds):
    record = json.loads(licensing.license_file().read_text())
    record["last_ok_at"] = time.time() - seconds
    licensing.license_file().write_text(json.dumps(record))


def test_network_failure_inside_the_grace_period_stays_active(polar, monkeypatch):
    licensing.activate(KEY)
    _age_last_check(2 * 24 * 3600)
    monkeypatch.setenv("NEXUS_LICENSE_API", "http://127.0.0.1:1")
    state = licensing.validate()
    assert state.status == licensing.ACTIVE
    assert "last good check" in state.message


def test_network_failure_after_the_grace_period_gives_unverified(polar, monkeypatch):
    licensing.activate(KEY)
    _age_last_check(8 * 24 * 3600)
    monkeypatch.setenv("NEXUS_LICENSE_API", "http://127.0.0.1:1")
    assert licensing.validate().status == licensing.UNVERIFIED


def test_server_error_counts_as_a_network_failure(polar):
    licensing.activate(KEY)
    _age_last_check(2 * 24 * 3600)
    polar.routes[VALIDATE] = (503, {})
    assert licensing.validate().status == licensing.ACTIVE


def test_offline_mode_makes_no_call(polar, monkeypatch):
    licensing.activate(KEY)
    polar.calls.clear()
    monkeypatch.setenv("NEXUS_OFFLINE", "true")
    _age_last_check(8 * 24 * 3600)
    state = licensing.validate(force=True)
    assert state.status == licensing.UNVERIFIED
    assert state.message == "offline mode"
    assert polar.calls == []


def test_unset_organization_makes_no_call(polar, monkeypatch):
    monkeypatch.setenv("NEXUS_LICENSE_ORG", "")
    monkeypatch.setattr(licensing, "POLAR_ORG_ID", "UNSET")
    monkeypatch.setenv("NEXUS_LICENSE_KEY", KEY)
    assert licensing.validate().status == licensing.UNVERIFIED
    assert licensing.activate(KEY).status == licensing.UNVERIFIED
    assert polar.calls == []


def test_deactivate_frees_the_activation_and_deletes_the_file(polar):
    licensing.activate(KEY)
    note = licensing.deactivate()
    assert "activation removed" in note
    assert _paths(polar)[-1] == DEACTIVATE
    assert not licensing.license_file().exists()


def test_current_state_reads_the_saved_record_without_the_network(polar, monkeypatch):
    licensing.activate(KEY)
    licensing.reset_state()
    monkeypatch.setattr(licensing, "_post", lambda *a, **k: pytest.fail("network used"))
    assert licensing.current_state().status == licensing.ACTIVE
    _age_last_check(8 * 24 * 3600)
    assert licensing.current_state().status == licensing.UNVERIFIED


def test_background_validation_updates_the_cached_state(polar, monkeypatch):
    monkeypatch.setenv("NEXUS_LICENSE_KEY", KEY)
    assert licensing.start_background_validation() is True
    for _ in range(100):
        if licensing.current_state().message == "licence is active":
            break
        time.sleep(0.05)
    assert licensing.current_state().status == licensing.ACTIVE


def test_status_has_no_license_field_without_a_key(polar):
    assert "license" not in core_api.status()


def test_status_license_field_is_one_word_with_a_key(polar):
    keys_without = set(core_api.status())
    licensing.activate(KEY)
    result = core_api.status()
    assert result["license"] == "active"
    assert set(result) - keys_without == {"license"}


def test_cli_license_status_without_a_key_exits_0(polar, capsys):
    assert cli.dispatch(["license", "status"]) == 0
    out = capsys.readouterr().out
    assert "status:   none" in out
    assert licensing.PRICING_URL in out


def test_cli_license_activate_then_status(polar, capsys):
    assert cli.dispatch(["license", "activate", KEY]) == 0
    assert "active" in capsys.readouterr().out
    assert cli.dispatch(["license", "status"]) == 0
    assert "status:   active" in capsys.readouterr().out


def test_cli_license_activate_bad_key_exits_1(polar):
    polar.routes[ACTIVATE] = (404, {})
    assert cli.dispatch(["license", "activate", KEY]) == 1


def test_cli_license_activate_names_the_host_before_the_call(polar, capsys):
    cli.dispatch(["license", "activate", KEY])
    assert "Contacting 127.0.0.1" in capsys.readouterr().err


def test_cli_license_deactivate_exits_0(polar, capsys):
    licensing.activate(KEY)
    assert cli.dispatch(["license", "deactivate"]) == 0
    assert not licensing.license_file().exists()


def test_notice_names_the_pricing_url():
    assert licensing.PRICING_URL in licensing.notice()
