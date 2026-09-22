# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Theme state management for semantic CSS variable themes.

One theme, "Performance", with a light/dark mode toggle.

ThemeState manages the user's light/dark mode preference. The theme is mapped
to CSS variables defined in assets/css/themes.css. Theme classes are applied
directly to document.body via rx.call_script so that CSS variables cascade
through the entire component tree.

Usage:
  ThemeState.toggle_mode()                    # Toggle light/dark
  ThemeState.apply_current_theme              # Use as on_load handler on each page
"""

import json

import reflex as rx
from pydantic import BaseModel

LOCAL_STORAGE_KEY = "chat_tree_user_info"


class LocalStorageUserInfo(BaseModel):
    """Consolidated user info stored in chat_tree_user_info localStorage."""

    theme: str = "theme-performance"
    mode: str = "dark"
    auth_authenticated: str = ""
    auth_email: str = ""


VALID_THEMES = ["theme-performance"]

# Pre-built JS snippet that removes all known theme/mode classes then adds the new ones.
# Uses classList so any other body classes are preserved.
REMOVE_ALL_THEMES_SCRIPT = (
    f"{str(VALID_THEMES)}"
    ".forEach(t=>document.body.classList.remove(t));"
    "document.body.classList.remove('light','dark');"
)


def compose_theme_init_script() -> str:
    """Apply theme classes synchronously before components render (prevents flash)."""
    return f"""
var userInfo = JSON.parse(localStorage.getItem('{LOCAL_STORAGE_KEY}') || '{{}}');
var savedTheme = userInfo.theme || 'theme-performance';
var savedMode = userInfo.mode || 'dark';
{REMOVE_ALL_THEMES_SCRIPT}
document.body.classList.add(savedTheme,savedMode);

// Sync with Reflex's ThemeProvider: set individual keys for compatibility
// This allows Reflex's react-theme.js to read the mode value without conflicts
localStorage.setItem('theme', savedMode);
"""


def compose_apply_theme_script(theme: str, mode: str) -> str:
    return f"""
{REMOVE_ALL_THEMES_SCRIPT}
document.body.classList.add('{theme}','{mode}');
localStorage.setItem('theme', '{mode}');
"""


class ThemeState(rx.State):
    """Manages theme mode (light/dark) with consolidated localStorage sync."""

    selected_theme: str = "theme-performance"
    selected_mode: str = "dark"

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

    def set_theme(self, theme: str):
        """Set the selected theme and sync to consolidated localStorage."""
        if theme in VALID_THEMES and theme != self.selected_theme:
            self.selected_theme = theme
            self.set_local_storage_user_info(**{"theme": theme})
            return rx.call_script(compose_apply_theme_script(theme, self.selected_mode))

    def toggle_mode(self):
        """Toggle between light and dark mode and sync to consolidated localStorage."""
        new_mode = "dark" if self.selected_mode == "light" else "light"
        self.selected_mode = new_mode
        self.set_local_storage_user_info(**{"mode": new_mode})
        return rx.call_script(compose_apply_theme_script(self.selected_theme, new_mode))

    def set_mode(self, mode: str):
        """Set light or dark mode and sync to consolidated localStorage."""
        if mode in ["light", "dark"]:
            self.selected_mode = mode
            self.set_local_storage_user_info(**{"mode": mode})
            return rx.call_script(compose_apply_theme_script(self.selected_theme, mode))

    def apply_current_theme(self):
        """Initialize theme from consolidated localStorage on page load.

        Also syncs the server-side vars from localStorage so components bound
        to selected_mode (e.g. the canvas `mode` prop) match the applied theme.
        """
        info = self.get_local_storage_user_info()
        self.selected_theme = info.theme if info.theme in VALID_THEMES else "theme-performance"
        self.selected_mode = info.mode if info.mode in ("light", "dark") else "dark"
        return rx.call_script(
            compose_apply_theme_script(self.selected_theme, self.selected_mode)
        )
