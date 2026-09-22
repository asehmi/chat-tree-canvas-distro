# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

import reflex as rx

from app_rx.components.auth.auth_page import _authenticating_callback
from app_rx.pages.index import index
from app_rx.states import AuthState, CanvasState, ThemeState
from app_rx.states.theme_state import compose_theme_init_script


def auth_callback() -> rx.Component:
    """Auth0 redirect target: shows a loading card while tokens are exchanged."""
    return rx.box(
        rx.script(compose_theme_init_script()),
        _authenticating_callback(),
    )


app = rx.App(
    stylesheets=[
        "/css/themes.css",
        "/css/canvas-bridge.css",
        "/css/styles.css",
    ],
)

app.add_page(
    index,
    route="/",
    title="Chat Tree Canvas",
    on_load=[
        ThemeState.apply_current_theme,
        AuthState.on_load,
        CanvasState.on_load,
    ],
)

# Kept at a fixed /app/callback path so the Auth0 application's allowed
# callback URLs never need to change between environments.
app.add_page(
    auth_callback,
    route="/app/callback",
    title="Authenticating…",
    # Theme first so the card is styled while the token exchange runs.
    on_load=[ThemeState.apply_current_theme, AuthState.on_auth_callback],
)
