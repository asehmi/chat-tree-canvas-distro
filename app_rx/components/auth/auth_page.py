# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Auth page components: sign-in badge and sign-out control."""

import reflex as rx

from app_rx.states.auth_state import AuthState


def _sign_in_badge() -> rx.Component:
    """Sign-in badge component positioned in the middle top third of the screen."""
    return rx.box(
        rx.center(
            rx.box(
                rx.vstack(
                    rx.box(
                        rx.icon("shield-alert", size=48, class_name="text-highlight"),
                    ),
                    rx.heading(
                        "Sign In Required",
                        class_name="text-4xl font-bold text-center mb-2 text-primary",
                    ),
                    rx.button(
                        rx.hstack(
                            rx.icon("log-in", size=20),
                            rx.text("Sign In"),
                            class_name="gap-2",
                        ),
                        on_click=AuthState.login,
                        class_name="w-[50%] bg-highlight text-default cursor-pointer hover:bg-highlight/20 transition-colors duration-200 py-2 px-3 rounded-md font-medium",
                    ),
                    rx.text(
                        "Please sign in to access your chat canvas.",
                        size="3",
                        class_name="text-default text-center",
                    ),
                    class_name="flex flex-col items-center gap-4",
                ),
                class_name="w-[90%] md:w-[350px] p-8 bg-secondary border border-primary rounded-lg shadow-md hover:shadow-lg hover:border-primary-accent cursor-pointer transition-shadow duration-200",
                on_click=AuthState.login,
            ),
            class_name="w-full h-[100vh] flex items-center justify-center bg-light",
        ),
    )


# This callback is registered on the "/app/callback" route, which is
# triggered by Auth0 after successful authentication. It is used to display
# a loading state while the app retrieves user credentials and sets up the session.
def _authenticating_callback() -> rx.Component:
    """Authenticating callback component positioned in the middle top third of the screen."""
    return rx.box(
        rx.center(
            rx.box(
                rx.vstack(
                    rx.box(
                        rx.icon("shield-alert", size=48, class_name="text-highlight"),
                    ),
                    rx.heading(
                        "Authenticating...",
                        class_name="text-4xl font-bold text-center mb-2 text-primary",
                    ),
                    rx.text(
                        "Retrieving login credentials...",
                        size="3",
                        class_name="text-default text-center",
                    ),
                    class_name="flex flex-col items-center gap-4",
                ),
                class_name="w-[90%] md:w-[350px] p-8 bg-secondary border border-primary rounded-lg shadow-md hover:shadow-lg hover:border-primary-accent transition-shadow duration-200",
            ),
            class_name="w-full h-[100vh] flex items-center justify-center bg-light",
        ),
    )
