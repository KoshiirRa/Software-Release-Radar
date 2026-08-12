from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlsplit


def _as_bool(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def insecure_integrations_allowed() -> bool:
    """Return whether credential-bearing cleartext integrations are explicitly allowed."""
    return _as_bool(os.environ.get("ALLOW_INSECURE_INTEGRATIONS"))


def _is_loopback_host(host: str | None) -> bool:
    value = str(host or "").strip().lower().rstrip(".")
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def _parsed_http_url(value: str, *, label: str):
    raw = str(value or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"{label} must be a complete http:// or https:// URL.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{label} must not contain embedded credentials.")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(f"{label} contains an invalid port.") from exc
    return raw, parsed


def trusted_application_origin(value: str, *, required: bool = True) -> str | None:
    """Validate and normalise the trusted origin used for password-reset links."""
    raw = str(value or "").strip()
    if not raw:
        if required:
            raise ValueError(
                "Application base URL is required before password-reset email can be used."
            )
        return None

    _, parsed = _parsed_http_url(raw, label="Application base URL")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(
            "Application base URL must contain only the scheme, hostname, and optional port."
        )
    if (
        parsed.scheme == "http"
        and not _is_loopback_host(parsed.hostname)
        and not insecure_integrations_allowed()
    ):
        raise ValueError(
            "Application base URL must use HTTPS. Set ALLOW_INSECURE_INTEGRATIONS=true "
            "only for an explicitly accepted trusted-network exception."
        )

    host = str(parsed.hostname)
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = host if port in {None, default_port} else f"{host}:{port}"
    return f"{parsed.scheme}://{netloc}"


def validate_credential_url(
    value: str,
    *,
    credential_present: bool,
    label: str,
) -> str:
    """Reject credential-bearing cleartext HTTP unless the deployment opted in."""
    raw, parsed = _parsed_http_url(value, label=label)
    if (
        credential_present
        and parsed.scheme == "http"
        and not _is_loopback_host(parsed.hostname)
        and not insecure_integrations_allowed()
    ):
        raise ValueError(
            f"{label} must use HTTPS when a reusable credential is configured. "
            "Set ALLOW_INSECURE_INTEGRATIONS=true only for an explicitly accepted "
            "trusted-network exception."
        )
    return raw


def validate_smtp_transport(
    host: str,
    security: str,
    *,
    username_present: bool,
    password_present: bool,
) -> None:
    """Require encrypted SMTP transport except for a local relay or explicit opt-in."""
    mode = str(security or "").strip().lower()
    if mode in {"starttls", "ssl"}:
        return
    if mode != "none":
        raise ValueError("Invalid SMTP security mode.")
    if _is_loopback_host(host) and not username_present and not password_present:
        return
    if insecure_integrations_allowed():
        return
    raise ValueError(
        "SMTP without TLS is permitted only for an unauthenticated loopback relay. "
        "Set ALLOW_INSECURE_INTEGRATIONS=true only for an explicitly accepted "
        "trusted-network exception."
    )
