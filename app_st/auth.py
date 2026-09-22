# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Auth0 identity for the Streamlit host — mirrors app_rx/states/auth_state.py.

st.login("auth0") drives Streamlit's built-in OIDC authorization-code flow
(Authlib under the hood) and makes st.user.email / st.user.name available.
Requires the [auth] + [auth.auth0] sections in .streamlit/secrets.toml (see
.streamlit/secrets.toml.example).

Auth0 login here is used ONLY to establish a stable real identity — the
email is handed straight to chat_tree.api_client.mint_session(email), the
same simple session-minting flow app_rx uses.

Auth0 has no standard OIDC end_session_endpoint on this tenant, so signing
out is a two-hop dance:
  1. Redirect the browser to Auth0's own /v2/logout, with returnTo carrying
     a marker query param.
  2. Auth0 clears its own session and redirects back here with that marker
     present; require_identity() finishes the local half (st.logout()) and
     lands on a clean URL.
"""

from __future__ import annotations

import os
import urllib.parse

import streamlit as st

from chat_tree import api_client

_POST_AUTH0_LOGOUT_PARAM = "post_auth0_logout"


def _auth0_domain() -> str:
    return os.getenv("AUTH0_DOMAIN", "")


def _auth0_client_id() -> str:
    return os.getenv("AUTH0_CLIENT_ID", "")


def _app_base_url() -> str:
    """Derive this app's own base URL from the redirect_uri already
    configured in .streamlit/secrets.toml, rather than a second setting that
    could drift out of sync with it."""
    try:
        redirect_uri = st.secrets["auth"]["redirect_uri"]
    except Exception:
        redirect_uri = "http://localhost:8765/oauth2callback"
    return redirect_uri.rsplit("/oauth2callback", 1)[0] or "http://localhost:8765"


def _auth0_logout_url() -> str:
    """Build Auth0's proprietary /v2/logout URL.

    NOTE: returnTo must match an entry in the Auth0 Application's "Allowed
    Logout URLs" — confirmed against the tenant already in use by the
    reference app: the plain origin (no trailing slash, no wildcard) is
    sufficient; Auth0 ignores the query string when matching.
    """
    return_to = f"{_app_base_url()}/?{_POST_AUTH0_LOGOUT_PARAM}=1"
    params = urllib.parse.urlencode({"client_id": _auth0_client_id(), "returnTo": return_to})
    return f"https://{_auth0_domain()}/v2/logout?{params}"


def _redirect(url: str) -> None:
    """Force a full top-level browser redirect (Streamlit has no native API
    for navigating to an arbitrary external URL)."""
    st.markdown(f'<meta http-equiv="refresh" content="0; url={url}">', unsafe_allow_html=True)


def auth0_configured() -> bool:
    return bool(_auth0_domain())


def require_identity() -> str:
    """Return the user_id chat_tree should use, gating the page behind Auth0
    login when configured. Dev fallback (AUTH0_DOMAIN unset) is
    chat_tree.api_client.default_user_id() — same policy as app_rx's
    CanvasState._identity.
    """
    if not auth0_configured():
        return api_client.default_user_id()

    if st.query_params.get(_POST_AUTH0_LOGOUT_PARAM) == "1":
        st.query_params.clear()
        st.logout()
        st.stop()

    if not st.user.is_logged_in:
        _, col, _ = st.columns([1, 2, 1])
        with col:
            st.title(":material/hub: Chat Tree Canvas")
            st.caption("Sign in to continue.")
            if st.button("Sign in with Auth0", use_container_width=True, type="primary"):
                st.login("auth0")
        st.stop()

    email = getattr(st.user, "email", None)
    if not email:
        st.error("Auth0 login succeeded but no email claim was returned.")
        st.stop()
    return email


def sign_out_button() -> None:
    """Sidebar sign-out control. Only call when auth0_configured()."""
    name = getattr(st.user, "name", None) or getattr(st.user, "email", None) or "User"
    st.caption(f"Signed in as **{name}**")
    if st.button(":material/logout: Sign out", use_container_width=True):
        st.session_state.pop("session_token", None)
        if _auth0_client_id():
            # Full logout: Auth0's own session first (see _auth0_logout_url),
            # then the local Streamlit session on the way back (handled in
            # require_identity via the post_auth0_logout marker).
            _redirect(_auth0_logout_url())
            st.stop()
        else:
            # Defensive fallback if AUTH0_CLIENT_ID is somehow unset while
            # AUTH0_DOMAIN is — local-only logout.
            st.logout()
