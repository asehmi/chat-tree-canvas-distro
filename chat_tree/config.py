# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Host policy flags, read once from the environment at import time."""

import os

# Cascading reruns: when true, the duplicate/rerun node buttons are offered on
# ALL nodes and a rerun cascades breadth-first through descendants; when false,
# both revert to leaf-only (cascading reruns on deep/wide trees can get very
# token-heavy and expensive). Read once at startup from .env.
CASCADING_RERUNS = (
    os.getenv("CASCADING_RERUNS", "true").strip().lower() in ("1", "true", "yes")
)
