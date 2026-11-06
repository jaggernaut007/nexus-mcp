"""Commercial licence key check (ADR-022).

Nexus-MCP is free for noncommercial use. A commercial user buys a monthly licence key from
Polar and sets it here. The check is a convenience, not a lock:

- Nothing is ever blocked. A missing, invalid or unverified key changes only a status word.
- With no key, the code makes no network call.
- With a key, the only data sent to ``api.polar.sh`` is the key, the Polar organization id,
  and an activation label made of ``nexus-mcp-ci`` and a random machine id.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Public values, not secrets: the Polar organization id and the Polar checkout link.
POLAR_ORG_ID = "3442192f-05d2-49f3-9a28-b6f4890f712b"
PRICING_URL = "https://github.com/jaggernaut007/Nexus-MCP#pricing"
CHECKOUT_URL = "https://buy.polar.sh/polar_cl_YmdKkkbt5AVAAHKefru7iUz9qpXBctKLCW0xF3hDZGL"
API_BASE = "https://api.polar.sh"
PACKAGE_LABEL = "nexus-mcp-ci"

VALIDATE_EVERY = 24 * 3600  # check at most once a day
GRACE = 7 * 24 * 3600  # a network failure keeps the state `active` for a week
TIMEOUT = 5  # seconds

NONE, ACTIVE, INVALID, REVOKED, UNVERIFIED = "none", "active", "invalid", "revoked", "unverified"
_TRUE = ("1", "true", "yes")


@dataclass
class LicenseState:
    status: str = NONE
    display_key: Optional[str] = None
    expires_at: Optional[str] = None
    checked_at: Optional[float] = None
    message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "display_key": self.display_key,
            "expires_at": self.expires_at,
            "checked_at": self.checked_at,
            "message": self.message,
        }


class NetworkError(Exception):
    """The licence server could not be reached or answered with a server error."""


# --- configuration ----------------------------------------------------------------


def api_base() -> str:
    return (os.environ.get("NEXUS_LICENSE_API") or API_BASE).rstrip("/")


def org_id() -> str:
    return os.environ.get("NEXUS_LICENSE_ORG") or POLAR_ORG_ID


def configured() -> bool:
    """False until the owner has put the Polar organization id into the build."""
    return org_id() not in ("", "UNSET")


def offline() -> bool:
    return os.environ.get("NEXUS_OFFLINE", "").lower() in _TRUE


def license_file() -> Path:
    """``license.json``. Set ``NEXUS_LICENSE_HOME`` to move it."""
    home = os.environ.get("NEXUS_LICENSE_HOME") or Path.home() / ".nexus-mcp"
    return Path(home) / "license.json"


# --- stored record ----------------------------------------------------------------


def _load() -> Dict[str, Any]:
    try:
        data = json.loads(license_file().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(record: Dict[str, Any]) -> None:
    path = license_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    # Create the file with owner-only rights before any secret is written to it.
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(record, fh, indent=2)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def resolve_key() -> Tuple[Optional[str], str]:
    """The key to use and where it came from: the environment first, then the saved file."""
    env = (os.environ.get("NEXUS_LICENSE_KEY") or "").strip()
    # A client can pass an unfilled template such as `${user_config.license_key}`. It is no key.
    if env and not env.startswith("${"):
        return env, "env"
    saved = _load().get("key")
    if isinstance(saved, str) and saved:
        return saved, "file"
    return None, "none"


def _mask(key: str) -> str:
    return "****-" + key[-6:] if len(key) > 6 else "****"


# --- network ----------------------------------------------------------------------


def _post(path: str, payload: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """POST JSON. Returns (status, body). A 4xx answer is returned; anything else raises."""
    req = urllib.request.Request(
        api_base() + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "nexus-mcp-ci"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            raw = resp.read()
            code = resp.status
    except urllib.error.HTTPError as exc:
        if 400 <= exc.code < 500:
            return exc.code, {}
        raise NetworkError(f"licence server error {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise NetworkError(str(exc)) from exc
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        body = {}
    return code, body if isinstance(body, dict) else {}


def _machine_id(record: Dict[str, Any]) -> str:
    mid = record.get("machine_id")
    if not isinstance(mid, str) or not mid:
        mid = uuid.uuid4().hex[:12]
    return mid


def _status_from(body: Dict[str, Any]) -> LicenseState:
    """Map a Polar licence key body to a state. Only ``granted`` counts as active."""
    key = body.get("key") or ""
    display = body.get("display_key") or (_mask(key) if key else None)
    expires = body.get("expires_at")
    if body.get("status") == "granted":
        return LicenseState(ACTIVE, display, expires, time.time(), "licence is active")
    return LicenseState(REVOKED, display, expires, time.time(), "licence is not active")


# --- operations -------------------------------------------------------------------


def _activate(key: str, record: Dict[str, Any]) -> Tuple[LicenseState, Dict[str, Any]]:
    mid = _machine_id(record)
    code, body = _post(
        "/v1/customer-portal/license-keys/activate",
        {"key": key, "organization_id": org_id(), "label": f"{PACKAGE_LABEL}-{mid}"},
    )
    if code == 403:
        msg = "activation limit reached; run `nexus-mcp-ci license deactivate` on another machine"
        return LicenseState(INVALID, _mask(key), None, time.time(), msg), record
    if code != 200 or not body.get("id"):
        return LicenseState(INVALID, _mask(key), None, time.time(), "key was not accepted"), record
    lic = body.get("license_key") or {}
    state = _status_from({**lic, "key": key})
    record = {
        **record,
        "machine_id": mid,
        "activation_id": body["id"],
        "key_sha256": _digest(key),
        "display_key": state.display_key,
        "expires_at": state.expires_at,
    }
    if state.status == ACTIVE:
        record["last_ok_at"] = state.checked_at
    return state, record


def activate(key: str) -> LicenseState:
    """Activate ``key`` on this machine and save it. Raises ``NetworkError`` when offline."""
    key = key.strip()
    if not configured():
        return LicenseState(
            UNVERIFIED, _mask(key), None, None, "commercial licensing is not open yet"
        )
    if offline():
        return LicenseState(UNVERIFIED, _mask(key), None, None, "offline mode")
    record = _load()
    state, record = _activate(key, record)
    if state.status == ACTIVE:
        record["key"] = key
        _save(record)
    return state


def validate(force: bool = False) -> LicenseState:
    """Check the key with Polar. Network use happens only when a key is set."""
    key, source = resolve_key()
    if not key:
        return LicenseState(NONE, None, None, None, "no licence key set")
    record = _load()
    same_key = record.get("key_sha256") == _digest(key)
    last_ok = record.get("last_ok_at") if same_key else None
    last_ok = last_ok if isinstance(last_ok, (int, float)) else None
    now = time.time()

    if not configured() or offline():
        reason = "offline mode" if offline() else "commercial licensing is not open yet"
        return _offline_state(key, record, last_ok, now, reason)
    if not force and last_ok is not None and now - last_ok < VALIDATE_EVERY:
        display = record.get("display_key") or _mask(key)
        return LicenseState(ACTIVE, display, record.get("expires_at"), last_ok, "cached")

    try:
        if not same_key or not record.get("activation_id"):
            # A key from the environment is activated once, on its first check.
            base = {"machine_id": record["machine_id"]} if record.get("machine_id") else {}
            state, record = _activate(key, base)
            code = 200 if state.status == ACTIVE else 400
            body: Dict[str, Any] = {}
        else:
            code, body = _post(
                "/v1/customer-portal/license-keys/validate",
                {
                    "key": key,
                    "organization_id": org_id(),
                    "activation_id": record["activation_id"],
                },
            )
            state = _status_from({**body, "key": key}) if code == 200 else LicenseState()
    except NetworkError as exc:
        return _offline_state(
            key, record, last_ok, now, f"could not reach the licence server ({exc})"
        )

    if code != 200 and state.status == NONE:
        return LicenseState(INVALID, _mask(key), None, now, "key was not accepted")
    if state.status == ACTIVE:
        record.update(last_ok_at=state.checked_at, expires_at=state.expires_at)
        record["display_key"] = state.display_key
        if source == "file":
            record["key"] = key
        _save(record)
    return state


def _offline_state(
    key: str, record: Dict[str, Any], last_ok: Optional[float], now: float, reason: str
) -> LicenseState:
    """No answer from the server: stay `active` for a week after the last good check."""
    display = record.get("display_key") or _mask(key)
    if last_ok is not None and now - last_ok < GRACE:
        return LicenseState(
            ACTIVE,
            display,
            record.get("expires_at"),
            last_ok,
            f"{reason}; using the last good check",
        )
    return LicenseState(UNVERIFIED, display, record.get("expires_at"), last_ok, reason)


def deactivate() -> str:
    """Free the activation on Polar and delete the saved file. Returns a one-line result."""
    record = _load()
    key, _ = resolve_key()
    note = "no saved licence"
    if key and record.get("activation_id") and configured() and not offline():
        try:
            code, _ = _post(
                "/v1/customer-portal/license-keys/deactivate",
                {
                    "key": key,
                    "organization_id": org_id(),
                    "activation_id": record["activation_id"],
                },
            )
            note = "activation removed" if code in (200, 204) else "activation was already gone"
        except NetworkError as exc:
            note = f"could not reach the licence server ({exc}); activation not removed"
    try:
        license_file().unlink()
        note += "; saved file deleted"
    except FileNotFoundError:
        pass
    return note


# --- cached state and server hooks ------------------------------------------------

_state: Optional[LicenseState] = None
_lock = threading.Lock()  # guards `_state`, which the validation thread and status() both use


def current_state() -> LicenseState:
    """The last known state. Never calls the network.

    Before the first check ends, it reads the saved record: a good check in the last week
    gives `active`, any other saved key gives `unverified`, and no key gives `none`.
    """
    with _lock:
        if _state is not None:
            return _state
    key, _ = resolve_key()
    if not key:
        return LicenseState()
    record = _load()
    last_ok = record.get("last_ok_at")
    if isinstance(last_ok, (int, float)) and time.time() - last_ok < GRACE:
        return LicenseState(ACTIVE, record.get("display_key") or _mask(key), None, last_ok, "saved")
    return LicenseState(
        UNVERIFIED, record.get("display_key") or _mask(key), None, None, "not checked yet"
    )


def _run_validation() -> None:
    global _state
    try:
        state = validate()
    except Exception as exc:  # a licence check must never take the server down
        state = LicenseState(UNVERIFIED, None, None, None, f"check failed ({type(exc).__name__})")
    with _lock:
        _state = state


def start_background_validation() -> bool:
    """Start the check in a daemon thread when a key is set. Returns True when it started."""
    key, _ = resolve_key()
    if not key:
        return False
    threading.Thread(target=_run_validation, name="nexus-license", daemon=True).start()
    return True


def notice() -> str:
    """One line for stderr when no key is set. It does not go into the agent context."""
    return f"Nexus-MCP is free for noncommercial use. Commercial use needs a licence: {PRICING_URL}"


def reset_state() -> None:
    """Forget the cached state (used by tests)."""
    global _state
    with _lock:
        _state = None


# --- CLI --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nexus-mcp-ci license",
        description=(
            "Commercial licence key. Free for noncommercial use; commercial use needs a paid "
            f"key ({PRICING_URL}). Nothing is ever blocked."
        ),
        epilog="With no key, Nexus-MCP makes no network call.",
    )
    sub = p.add_subparsers(dest="action", required=True)
    act = sub.add_parser("activate", help="Activate a key on this machine and save it")
    act.add_argument("key", help="The licence key from your Polar receipt")
    sub.add_parser("status", help="Show the licence state (checks the server if a key is set)")
    sub.add_parser("deactivate", help="Free this machine's activation and delete the saved key")
    return p


def run_license(argv: List[str]) -> int:
    args = _parser().parse_args(argv)
    if args.action == "activate":
        if not configured():
            print("Commercial licensing is not open yet. Nothing was sent.", file=sys.stderr)
            return 1
        if offline():
            print("NEXUS_OFFLINE is set. Nothing was sent.", file=sys.stderr)
            return 1
        print(f"Contacting {api_base().split('//')[-1]} to activate this key.", file=sys.stderr)
        try:
            state = activate(args.key)
        except NetworkError as exc:
            print(f"error: could not reach the licence server ({exc})", file=sys.stderr)
            return 1
        print(f"{state.status}: {state.message} ({state.display_key})")
        return 0 if state.status == ACTIVE else 1
    if args.action == "deactivate":
        key, _ = resolve_key()
        if key and configured() and not offline():
            print(
                f"Contacting {api_base().split('//')[-1]} to deactivate this key.", file=sys.stderr
            )
        print(deactivate())
        return 0
    key, _ = resolve_key()
    if key and configured() and not offline():
        print(f"Contacting {api_base().split('//')[-1]} to check this key.", file=sys.stderr)
    state = validate(force=False)
    print(f"status:   {state.status}")
    print(f"key:      {state.display_key or '-'}")
    print(f"expires:  {state.expires_at or '-'}")
    when = (
        time.strftime("%Y-%m-%d %H:%M", time.localtime(state.checked_at))
        if state.checked_at
        else "-"
    )
    print(f"checked:  {when}")
    if state.message:
        print(f"note:     {state.message}")
    if state.status == NONE:
        print(f"Free for noncommercial use. Commercial use: {PRICING_URL}")
    return 0
