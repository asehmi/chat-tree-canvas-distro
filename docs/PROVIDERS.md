# Plugging your own chat backend into chat-tree-canvas

The canvas front end is backend-agnostic. Every request it makes — health
checks, session tokens, streaming chat, history hydration, round deletion —
goes through a small provider interface. This guide explains the contract and
the two ways to connect your own backend.

```
CanvasState  ──►  chat_tree/api_client.py (façade)
                        │
                        ▼
              chat_tree/providers/  (selected by CHAT_API_PROVIDER)
                ├── reference.py   the canonical minimal protocol (:9000)
                └── yours.py       anything else
```

## Configuration

| Env var             | Meaning                                             | Default                     |
|---------------------|-----------------------------------------------------|-----------------------------|
| `CHAT_API_PROVIDER` | Which provider talks to your backend                | `reference`                 |
| `CHAT_API_BASE_URL` | Backend base URL (overrides the provider's default) | per provider (:9000)        |
| `CHAT_API_USER_ID`  | Fallback identity when nobody is signed in          | provider-specific           |

Env vars are read once at startup — restart `reflex run` after changing them.

## The fastest route: implement the reference protocol

If you control your backend's HTTP surface, implement this protocol and you
need **zero Python changes** in the canvas — just set
`CHAT_API_PROVIDER=reference` and `CHAT_API_BASE_URL`.

All endpoints are JSON; authenticated calls carry an `X-Session-Token` header.

| Endpoint                              | Request                             | Response |
|---------------------------------------|-------------------------------------|----------|
| `GET /health`                          | —                                   | any 200 when ready |
| `POST /session`                        | `{"user_id": str}`                  | `{"session_token": str}` |
| `GET /history/{node_id}`               | —                                   | `{"messages": [row, …]}` |
| `DELETE /history/{node_id}/{round}`    | —                                   | 200 (404 if unknown) |
| `POST /chat`                           | `{"node_id": str, "message": str}`  | `text/event-stream` of `data: {json}\n\n` |

**`mock_provider.py` at the repo root is a complete FastAPI implementation**
— copy it as your starting point, or run it as-is to drive the canvas with a
canned LLM:

```
.venv\Scripts\uvicorn mock_provider:app --port 9000
# .env: CHAT_API_PROVIDER=reference  (CHAT_API_BASE_URL defaults to :9000)
reflex run
```

## Semantics your backend must honour

- **`node_id` keys one prompt/answer exchange.** Each canvas node is one
  exchange; the tree's conversational context arrives *inside the message
  text*, but as a **postfix**, not a prefix: the message always starts with
  the node's actual prompt, and only after it — behind a
  `--- HISTORICAL CONTEXT (latest first) ---` delimiter, `---`-separated,
  nearest ancestor first — do the ancestor chain's answers follow (see
  `chat_tree/context.py`). This is deliberate: if your backend does any
  intent/route classification on the message, it should look at the start
  of the text, not assume the "real" question is at the end. Your backend
  does not need to understand the tree.
- **Rounds.** Normally one round per node (round 0). Report the round number
  in the `done` event; the canvas passes it back to
  `DELETE /history/{node_id}/{round}` when the user reruns the node.
- **Sessions are per-identity.** `POST /session` is called with the signed-in
  user's id (or `CHAT_API_USER_ID`); the canvas discards the token when the
  identity changes. If you don't need auth, return any opaque string.
- **Hydration.** On page load / session load / import, the canvas calls
  `GET /history/{node_id}` for every node. Return `{"messages": []}` (or 404)
  for unknown nodes — the canvas shows those prompt-only and warns the user
  to rerun them.

## Stream events

Each SSE line is `data: {"type": ..., "data": {...}}`. The canvas folds them
into typed display blocks (rendered by `packages/chat-tree-canvas-react/src/renderers/`):

| type               | data                                                    | renders as |
|--------------------|---------------------------------------------------------|------------|
| `text_delta`       | `{"delta": str}`                                        | streaming markdown text |
| `route_decision`   | `{"mode": str}`                                         | intent badge in the node header |
| `tool_call`        | `{"tool": str}`                                         | tool status line (running…) |
| `tool_result`      | `{"tool": str, "denied": bool, "reason": str}`          | tool status line (✓ / denied) |
| `sandbox_start`    | `{"code": str}`                                         | collapsible script panel |
| `tool_call_result` | `{"kind": "markdown", "text": str}`                     | appended markdown |
|                    | `{"kind": "metric", "label", "value", "unit"}`          | stat tile |
|                    | `{"kind": "table", "title", "records": [dict]}`         | scrollable table |
|                    | `{"kind": "chart", "title", "figure_json": str}`        | Plotly chart (figure JSON) |
|                    | `{"kind": "mermaid", "title", "code": str}`             | mermaid diagram (source, rendered client-side) |
|                    | `{"kind": "question", "text": str}`                     | inline follow-up question — see BLOG_POST.md §12 |
| `result`           | `{"final_report": str, "charts": [{"type","json"}]}`    | report text + charts |
| `error`            | `{"message": str}`                                      | node error box |
| `done`             | `{"round": int}`                                        | ends the exchange |

**Minimum viable stream: `text_delta`\* followed by `done`.** Everything else
is optional garnish.

## History rows

`GET /history/{node_id}` returns rows shaped like:

```json
{"role": "user",      "text": "the submitted message"}
{"role": "assistant", "text": "full answer text",
 "events": [ …the stream events you emitted… ],
 "result": {}, "mode": "your_route", "round": 0, "interrupted": false}
```

The canvas rebuilds display blocks by replaying `events` (and `result`); if
both are absent it falls back to `text`. **Minimum viable assistant row:**
`{"role": "assistant", "text": str, "round": 0}`.

## The custom route: write a Provider subclass

If your backend's wire format can't match the reference protocol, adapt in
Python instead:

1. Create `chat_tree/providers/yours.py` subclassing `ChatProvider`
   (`base.py` documents every method). Implement: `base_url`,
   `check_health`, `mint_session`, `get_history`, `delete_round`,
   `stream_chat` (an async iterator yielding the event dicts above), and
   optionally `default_user_id`.
2. Register it in `_REGISTRY` in `chat_tree/providers/__init__.py` under a
   unique `name`.
3. Set `CHAT_API_PROVIDER=<name>` in `.env` and restart.

This is the route to take when your backend's paths, wire keys, or auth
model don't match the reference protocol closely enough to reuse as-is.

## Custom block kinds (front end)

Providers can emit their own block kinds: add a renderer file under
`packages/chat-tree-canvas-react/src/renderers/` and register it in `RENDERERS`
(`packages/chat-tree-canvas-react/src/renderers/index.jsx`). Unknown kinds are silently skipped, so new
backends degrade gracefully on older front ends. **Full walkthrough:
`docs/RENDERERS.md`.**

## Related

- Export/import format ("chat-tree-canvas/v1", coalesced root→leaf
  conversations): `chat_tree/exporter.py`.
- Terminology: `node_id` = one exchange; a *conversation* = a full
  root-to-leaf path in the tree.
