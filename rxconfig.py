# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

from pathlib import Path
from dotenv import load_dotenv, find_dotenv
import reflex as rx

# Load env
env_file = Path(__file__).parent / ".env"
load_dotenv(env_file)
load_dotenv(find_dotenv(env_file))

# chat-tree-canvas-react is consumed as a bun-linked (junction) package, so
# Vite serves its files from their real path OUTSIDE .web/. On a cold .web,
# Vite's dependency scanner doesn't crawl into linked packages, so the
# package's bare imports (reactflow, plotly, …) never get pre-bundled and
# fail import-analysis ("Failed to resolve import ... from ../packages/...").
# Fix per Vite's monorepo guidance: force them into optimizeDeps.include.
# vite.config.js is regenerated every compile, so this is applied as a
# Reflex plugin modify-task (same mechanism TailwindV4Plugin uses).
_CANVAS_DEPS = ["reactflow", "react-markdown", "remark-gfm", "plotly.js-dist-min", "mermaid"]


def _add_canvas_optimize_deps(vite_config: str) -> str:
    marker = "export default defineConfig((config) => ({"
    if "optimizeDeps" in vite_config or marker not in vite_config:
        return vite_config  # already patched, or template changed — leave alone
    deps = ", ".join(f'"{d}"' for d in _CANVAS_DEPS)
    inject = (
        f"{marker}\n"
        "  // Injected by rxconfig.py: pre-bundle the linked canvas package's deps.\n"
        f"  optimizeDeps: {{ include: [{deps}] }},\n"
    )
    return vite_config.replace(marker, inject, 1)


class CanvasLinkedDepsPlugin(rx.plugins.Plugin):
    """Patch the generated vite.config.js for the linked canvas package."""

    def pre_compile(self, **context):
        context["add_modify_task"]("vite.config.js", _add_canvas_optimize_deps)

config = rx.Config(
    app_name="app_rx",
    env=rx.Env.DEV,
    show_built_with_reflex=False,
    backend_port=8001,
    head_components=[
        rx.el.link(
            rel="preconnect",
            href="https://fonts.googleapis.com",
        ),
        rx.el.link(
            rel="preconnect",
            href="https://fonts.gstatic.com",
            cross_origin="anonymous",
        ),
        rx.el.link(
            href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&family=Inter:wght@400;500;600;700&display=swap",
            rel="stylesheet",
        ),
    ],
    # Tailwind with the semantic theme colors (CSS variables set per
    # body class by assets/css/themes.css — see app/states/theme_state.py).
    plugins=[
        rx.plugins.TailwindV4Plugin(
            config={
                "theme": {
                    "extend": {
                        "colors": {
                            "primary": "var(--primary)",
                            "primary-accent": "var(--primary-accent)",
                            "highlight": "var(--highlight)",
                            "secondary": "var(--secondary)",
                            "default": "var(--default)",
                            "dark": "var(--dark)",
                            "dim": "var(--dim)",
                            "light": "var(--light)",
                        },
                        "fontFamily": {
                            "display": "Poppins, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                            "sans": "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                        },
                    },
                },
            }
        ),
        CanvasLinkedDepsPlugin(),
    ],
    disable_plugins=["reflex.plugins.sitemap.SitemapPlugin"],
)
