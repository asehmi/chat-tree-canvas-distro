# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Node/edge factory helpers, plus the authenticated-user model.

Nodes and edges are kept as plain dicts in the exact shape ReactFlow expects,
so state vars can be passed straight through to the JSX component.
"""

import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

from pydantic import BaseModel

from chat_tree.utils import decode_id_token

NODE_WIDTH = 380
CHILD_Y_GAP = 320
SIBLING_X_GAP = NODE_WIDTH + 60


def new_id() -> str:
    return uuid.uuid4().hex


def make_node(
    x: float, y: float, node_id: str | None = None, prompt: str = ""
) -> Dict[str, Any]:
    return {
        "id": node_id or new_id(),
        "type": "chat",
        "position": {"x": x, "y": y},
        "data": {
            "prompt": prompt,
            "blocks": [],  # typed response blocks — see canvas_state docstring
            "status": "idle",  # idle | streaming | complete | error
            "error": "",
            "route": "",
            "round": 0,  # backend round number, captured from the done event
            "leaf": True,  # maintained by CanvasState._refresh_leaves
            "width": 0,  # 0 = default width; set by the resize control
            "height": 0,  # 0 = auto height
        },
    }


def make_edge(source: str, target: str) -> Dict[str, Any]:
    return {"id": f"e-{source}-{target}", "source": source, "target": target}


# ------------------------------------------------------------------------------
# Core data model for user session
# ------------------------------------------------------------------------------


@dataclass
class Role:
    """Simple role hierarchy for users."""

    guest = "guest"
    user = "user"
    admin = "admin"


class User(BaseModel):
    """
    Pure serializable Pydantic model for authenticated users.
    Contains only essential user profile data (no activity logging).
    """

    email: Optional[str] = None
    name: str = ""
    consent: bool = False
    role: str = Role.guest

    def init_from_token_info(self, token_info: dict) -> None:
        """
        Populate user fields from Auth0 token info.

        Args:
            token_info: Dictionary containing 'id_token' from Auth0
        """
        id_token = token_info.get("id_token", "")

        if id_token:
            token = decode_id_token(id_token)
            # Extract common Auth0 claim variations
            email = token.get("email") or token.get("user", {}).get("email") or ""
            name = token.get("name") or token.get("given_name") or token.get("nickname") or ""

            self.email = email
            self.name = name
            self.role = Role.user  # Default to 'user' role
        else:
            # Anonymous / unauthenticated user
            self.email = None
            self.name = "Guest"
            self.role = Role.guest

    def set_consent(self, consent: bool) -> None:
        """Set user consent."""
        self.consent = bool(consent)

    def set_role(self, new_role: str) -> None:
        """Manually set role (guest, user, admin)."""
        valid_roles = {Role.guest, Role.user, Role.admin}
        if new_role in valid_roles:
            self.role = new_role

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role."""
        return self.role == Role.admin

    @property
    def is_authenticated(self) -> bool:
        """Check if user is authenticated (has email set)."""
        return self.email is not None

    def as_dict(self) -> dict:
        """Return a serializable snapshot of user data."""
        return {
            "email": self.email,
            "name": self.name,
            "consent": self.consent,
            "role": self.role,
        }
