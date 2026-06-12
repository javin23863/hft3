"""Auth — two scopes, with control hard-pinned to local origin.

- ``view``    : read endpoints + WS. Bearer token (COCKPIT_VIEW_TOKEN).
- ``control`` : job triggers. Bearer token (COCKPIT_CONTROL_TOKEN) AND the
                request must originate locally (loopback client, no proxy
                forwarding header). A remote token-holder can NEVER hold
                control: a proxied request carries X-Forwarded-For, which
                disqualifies it regardless of token.

If no view token is configured, read access is allowed from loopback only
(dev convenience) and refused from remote — never silently world-open.
"""
from __future__ import annotations

import hmac
import os
from typing import Optional

from fastapi import Header, HTTPException, Request, status

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _view_token() -> Optional[str]:
    return os.environ.get("COCKPIT_VIEW_TOKEN") or None


def _control_token() -> Optional[str]:
    return os.environ.get("COCKPIT_CONTROL_TOKEN") or None


def _bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(" ", 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def _eq(a: Optional[str], b: Optional[str]) -> bool:
    if not a or not b:
        return False
    return hmac.compare_digest(a, b)


def _is_loopback(request: Request) -> bool:
    client = request.client.host if request.client else ""
    return client in _LOOPBACK


def _is_proxied(request: Request) -> bool:
    return "x-forwarded-for" in {k.lower() for k in request.headers.keys()}


def require_view(request: Request, authorization: Optional[str] = Header(default=None)) -> str:
    """Allow if a valid view/control bearer is presented, else loopback-only dev access."""
    token = _bearer(authorization)
    vt, ct = _view_token(), _control_token()
    if vt is None and ct is None:
        # No tokens configured: loopback dev access only.
        if _is_loopback(request) and not _is_proxied(request):
            return "view"
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "view token required for remote access")
    if _eq(token, vt) or _eq(token, ct):
        return "view"
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or missing view token")


def require_control(request: Request, authorization: Optional[str] = Header(default=None)) -> str:
    """Control: valid control token AND genuinely local origin (no proxy)."""
    if _is_proxied(request) or not _is_loopback(request):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "control is local-origin only")
    ct = _control_token()
    if ct is None:
        # No control token configured: allow only direct loopback (dev).
        return "control"
    if _eq(_bearer(authorization), ct):
        return "control"
    raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid control token")
