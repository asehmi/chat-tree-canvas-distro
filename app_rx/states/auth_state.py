# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Authentication state management using Auth0.

This app has no user database; identity flows straight into
CanvasState.user_id once Auth0 confirms it.

AuthState stores the authenticated user as a Pydantic model and exposes events
for user operations.

# Auth flow
AuthState.login()                        # Redirect to Auth0
AuthState.on_auth_callback()             # Called after Auth0 auth
AuthState.logout()                       # Clear state + Auth0 logout
# AuthState.backdoor_login()             # Admin bypass, disabled by default — see below

Computed Properties

AuthState.user: User | None              # The authenticated user object
AuthState.user_name: str                 # User's display name
AuthState.user_email: str                # User's email
AuthState.is_user_authenticated: bool    # Check if user logged in
AuthState.is_user_admin: bool            # Check if user is admin
"""

import json
import logging
import os
from urllib.parse import urlencode

import reflex as rx
from pydantic import BaseModel

from chat_tree.models import Role, User
from app_rx.states.theme_state import LOCAL_STORAGE_KEY

logger = logging.getLogger(__name__)


class LocalStorageUserInfo(BaseModel):
    """Consolidated user info stored in chat_tree_user_info localStorage."""

    theme: str = "theme-performance"
    mode: str = "dark"
    auth_authenticated: str = ""
    auth_email: str = ""


class AuthState(rx.State):
    """Authentication state managed by Auth0."""

    is_authenticated: bool = False
    token_info: dict = {}
    user: User | None = None

    # Consolidated user info stored as JSON string in localStorage
    user_info_json: str = rx.LocalStorage(name=LOCAL_STORAGE_KEY, sync=True)

    def get_local_storage_user_info(self) -> LocalStorageUserInfo:
        """Parse user info JSON string from localStorage."""
        try:
            data = json.loads(self.user_info_json) if self.user_info_json else {}
            return LocalStorageUserInfo(**data)
        except Exception:
            return LocalStorageUserInfo()

    def set_local_storage_user_info(self, **kwargs):
        """Update user info by merging with existing data and store as JSON string."""
        current = self.get_local_storage_user_info()
        updated = current.model_copy(update=kwargs)
        self.user_info_json = updated.model_dump_json()

    def on_load(self):
        """Initialize user state."""
        if not self.user:
            self.user = User()
            # Re-initialize user from stored token_info if available
            if self.token_info:
                self.user.init_from_token_info(self.token_info)

    @rx.var
    def user_name(self) -> str:
        """Get user name for display."""
        return self.user.name if self.user and self.user.name else "Guest"

    @rx.var
    def user_email(self) -> str:
        """Get user email for display."""
        return self.user.email if self.user and self.user.email else "Not logged in"

    @rx.var
    def name_initials(self) -> str:
        """Get initials from user name for avatar display."""
        if self.user:
            if not self.user.name:
                return "👤"
            parts = self.user.name.split()
            if len(parts) == 1:
                return parts[0][0].upper()
            return (parts[0][0] + parts[-1][0]).upper()
        return "👤"

    @rx.var
    def user_id(self) -> str:
        """Get the authenticated user's email, or 'anonymous' if not logged in."""
        if self.user and self.user.email:
            return self.user.email
        return "anonymous"

    @rx.event
    def login(self):
        """Redirect to Auth0 login page (in same window)."""
        domain = os.getenv("AUTH0_DOMAIN")
        client_id = os.getenv("AUTH0_CLIENT_ID")
        app_url = os.getenv("REFLEX_FE_BASE_URL", "http://localhost:3000")
        redirect_uri = f"{app_url}/app/callback"
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email",
            "audience": f"https://{domain}/api/v2/",
        }
        auth_url = f"https://{domain}/authorize?{urlencode(params)}"
        return rx.call_script(f"window.location.href = '{auth_url}';")

    @rx.event
    async def on_auth_callback(self):
        """Handle Auth0 callback after successful authentication."""
        from auth0.authentication import GetToken
        from auth0.exceptions import Auth0Error

        try:
            domain = os.getenv("AUTH0_DOMAIN")
            client_id = os.getenv("AUTH0_CLIENT_ID")
            client_secret = os.getenv("AUTH0_CLIENT_SECRET")
            app_url = os.getenv("REFLEX_FE_BASE_URL", "http://localhost:3000")
            redirect_uri = f"{app_url}/app/callback"
            code = self.router.url.query_parameters.get("code")

            if not code:
                yield rx.redirect("/")
                return

            get_token = GetToken(
                domain=domain, client_id=client_id, client_secret=client_secret
            )
            token_data = get_token.authorization_code(
                code=code, redirect_uri=redirect_uri
            )
            self.token_info = token_data
            self.is_authenticated = True

            # Initialize user from token info and store in state
            self.user = User()
            self.user.init_from_token_info(self.token_info)

            # Sync auth state to consolidated localStorage
            self.set_local_storage_user_info(**{
                "auth_authenticated": "true",
                "auth_email": (self.user.email or ""),
            })

            yield rx.redirect("/")

        except Auth0Error as e:
            logger.error(f"Auth0 Error: {e}", exc_info=True)
            yield rx.toast("Authentication failed. Please try again.")
            yield rx.redirect("/")
        except Exception as e:
            logger.error(f"Authorization Error: {e}", exc_info=True)
            yield rx.toast("Authentication failed. Please try again.")
            yield rx.redirect("/")

    @rx.event
    async def logout(self):
        """Logout: clear state and redirect to Auth0 logout endpoint (in same window)."""
        # Clear user data
        self.user = User()

        self.is_authenticated = False
        self.token_info = {}

        # Clear the canvas too, explicitly — don't rely on CanvasState.on_load's
        # identity-comparison to catch this indirectly. If CHAT_API_USER_ID
        # happens to match the account just signed out of, "desired" resolves
        # to the same value before and after logout, and on_load would see no
        # change and skip reloading, leaving the last session on screen after
        # sign-out. Blanking here is unconditional and doesn't depend on what
        # the dev-fallback identity happens to be.
        from app_rx.states.canvas_state import CanvasState

        canvas = await self.get_state(CanvasState)
        canvas.nodes = []
        canvas.edges = []
        canvas.session_id = ""
        canvas.loaded = False

        # Clear auth state from consolidated localStorage
        self.set_local_storage_user_info(**{
            "auth_authenticated": "",
            "auth_email": "",
        })

        domain = os.getenv("AUTH0_DOMAIN")
        client_id = os.getenv("AUTH0_CLIENT_ID")
        app_url = os.getenv("REFLEX_FE_BASE_URL", "http://localhost:3000")
        logout_url = f"{app_url}/"
        params = {"client_id": client_id, "returnTo": logout_url}
        logout_url_full = f"https://{domain}/v2/logout?{urlencode(params)}"
        return rx.call_script(f"window.location.href = '{logout_url_full}';")

    # Disabled in this public distro: these two handlers let an external
    # caller (e.g. a sidecar admin tool) sign in as an admin user without
    # going through Auth0, bypassing login entirely. Uncomment only if you
    # control who can reach this app's event endpoint.
    #
    # @rx.event
    # def backdoor_login(self):
    #     """Secret login for testing (admin access)."""
    #     self.user = User()
    #     self.user.role = Role.admin
    #     self.user.email = "admin@example.com"
    #     self.user.name = "admin"
    #
    #     id_token = os.getenv("ADMIN_ID_TOKEN")
    #     self.token_info = {"id_token": id_token}
    #     self.is_authenticated = True
    #
    # @rx.event
    # def backdoor_logout(self):
    #     """Secret logout for testing."""
    #     return self.logout()

    # Computed properties for UI access
    @rx.var
    def is_user_authenticated(self) -> bool:
        """Check if user is authenticated."""
        if self.user is None:
            return False
        return self.is_authenticated and self.user.email is not None

    @rx.var
    def is_user_admin(self) -> bool:
        """Check if user is an admin."""
        if self.user is None:
            return False
        return self.user.role == Role.admin
