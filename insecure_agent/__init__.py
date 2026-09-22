# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""The insecure agent provider — a real (unsandboxed) reference-protocol
backend for chat-tree-canvas. See insecure_agent_provider.py at the repo
root for the FastAPI app, and this package's own submodules for the pieces
it's built from. Not imported by chat_tree/, app_rx/, or app_st/ — this is
a standalone alternative backend, same as mock_provider.py.
"""
