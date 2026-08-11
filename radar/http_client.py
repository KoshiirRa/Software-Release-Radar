from __future__ import annotations

import ssl
import urllib.parse
import urllib.request
from typing import Any


_ALLOWED_SCHEMES = {"http", "https"}


def validate_http_url(value: str, *, label: str = "URL") -> str:
    """Return a normalised HTTP(S) URL or reject unsupported schemes."""
    url = str(value or "").strip()
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.hostname:
        raise ValueError(f"{label} must be a complete http:// or https:// URL.")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} must not contain embedded credentials.")
    return url


def open_http(
    request: urllib.request.Request | str,
    *,
    timeout: float,
    context: ssl.SSLContext | None = None,
) -> Any:
    """Open an HTTP(S) request after validating the destination scheme.

    Callers may intentionally reach private network addresses because local service
    probing and self-hosted integrations are product features. This helper limits
    urllib to HTTP(S) so file:, ftp: and custom URL handlers cannot be selected.
    """
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request)
    validate_http_url(url)
    return urllib.request.urlopen(request, timeout=timeout, context=context)  # nosec B310 - scheme and host are validated above.
