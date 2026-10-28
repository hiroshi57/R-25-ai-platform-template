"""Shared network guard for the stdlib HTTP callers.

``urllib.request.urlopen`` accepts any scheme the opener knows, including
``file:``. Both callers here take their URL from configuration (an
OpenAI-compatible endpoint, an Overpass endpoint), so a mistyped or injected
value could turn a "fetch" into a local file read. Validate the scheme once,
in one place, before opening.
"""

from __future__ import annotations

from urllib.parse import urlparse

ALLOWED_SCHEMES = ("http", "https")


def require_http_url(url: str, *, what: str) -> str:
    """Return ``url`` unchanged, or raise ``ValueError`` if it is not http(s).

    ``what`` names the setting in the error message so a misconfiguration is
    obvious without reading the traceback.
    """
    scheme = urlparse(url).scheme.lower()
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(
            f"{what} must use http or https, got {scheme or '(none)'}: {url!r}"
        )
    return url
