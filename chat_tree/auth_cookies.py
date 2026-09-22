# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""
Authentication cookie middleware.

Handles HTTP-only secure cookies for storing auth tokens.
Cookies are automatically sent by the browser on all requests.

Note: this app's Auth0 flow keeps token_info in Reflex server-side state, so
the middleware is not wired into an API by default — it is available for any
future FastAPI `api_transformer` endpoints that need cookie-based auth.
"""

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

logger = logging.getLogger(__name__)


# Cookie constants
AUTH_COOKIE_NAME = "auth_token"
COOKIE_MAX_AGE = 3600 * 24 * 7  # 7 days in seconds


class AuthCookieMiddleware(BaseHTTPMiddleware):
    """
    Middleware to handle authentication cookies.

    Features:
    - Cookies are HTTP-only (not accessible via JavaScript)
    - Cookies are secure (only sent over HTTPS in production)
    - Cookies are SameSite=lax (prevents CSRF attacks)
    - Cookies persist for 7 days
    """

    async def dispatch(self, request, call_next):
        """Process request and response, managing auth cookies."""
        response = await call_next(request)

        # The response object may have cookie-setting logic added by route handlers
        # This middleware just ensures proper cookie attributes
        return response

    @staticmethod
    def set_auth_cookie(response: Response, token: str, max_age: int = COOKIE_MAX_AGE, request=None) -> None:
        """
        Set authentication token as HTTP-only secure cookie.

        Args:
            response: Starlette Response object
            token: JWT auth token to store
            max_age: Cookie max age in seconds (default: 7 days)
            request: Optional Starlette Request object to auto-detect localhost

        Note:
            - Secure flag is only set for non-localhost hosts (HTTPS required in production)
            - For localhost development, HTTP cookies are allowed
        """
        # Auto-detect if we're on localhost - if so, don't require HTTPS
        secure = True
        if request:
            host = request.headers.get("host", "")
            if "localhost" in host or "127.0.0.1" in host:
                secure = False
                logger.debug(f"Localhost detected ({host}), setting secure=False for cookie")

        response.set_cookie(
            key=AUTH_COOKIE_NAME,
            value=token,
            httponly=True,      # Not accessible via JavaScript
            secure=secure,      # Only sent over HTTPS (false for localhost, true for production)
            samesite="lax",     # CSRF protection
            max_age=max_age,    # Cookie expires after N seconds
            path="/",           # Cookie valid for entire app
        )
        logger.debug(f"Set auth cookie with max_age={max_age}s, secure={secure}")

    @staticmethod
    def clear_auth_cookie(response: Response, request=None) -> None:
        """
        Clear authentication cookie (logout).

        Args:
            response: Starlette Response object
            request: Optional Starlette Request object to auto-detect localhost
        """
        # Auto-detect if we're on localhost
        secure = True
        if request:
            host = request.headers.get("host", "")
            if "localhost" in host or "127.0.0.1" in host:
                secure = False

        response.delete_cookie(
            key=AUTH_COOKIE_NAME,
            path="/",
            httponly=True,
            secure=secure,
            samesite="lax",
        )
        logger.debug(f"Cleared auth cookie (secure={secure})")

    @staticmethod
    def get_auth_cookie(request) -> str | None:
        """
        Read authentication token from cookie.

        Args:
            request: Starlette Request object

        Returns:
            Auth token string if present, None otherwise
        """
        return request.cookies.get(AUTH_COOKIE_NAME)
