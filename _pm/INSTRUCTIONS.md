# Build & install instructions

This repo ships **two interchangeable host apps** (`app_rx` = Reflex,
`app_st` = Streamlit) sharing one framework-free core (`chat_tree/`) and one
React canvas package (`packages/chat-tree-canvas-react/`). Both hosts read
and write the same SQLite file, so you can run either or both at once.

You only need to fully set up the host(s) you intend to run — see
"Running only app_rx" / "Running only app_st" at the end if you want to skip
the other one.

## 0. Prerequisites

- **Python 3.10+**
- **Node.js 18+** and npm (only needed to build the Streamlit component's
  frontend bundle — Reflex manages its own bun/Node toolchain internally)
- A chat backend for both hosts to talk to over HTTP/SSE. This repo ships
  two: `mock_provider.py` (canned responses, zero setup) and
  `insecure_agent_provider.py` (a real, LLM-backed agent — see the root
  `README.md`'s "Try a real agent" section). Neither needs anything beyond
  this repo and, for the latter, your own LLM API key.

## 1. Python environment

From the repo root:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

This installs both hosts' shared dependencies (Reflex, Streamlit, Auth0,
httpx, etc.) in one venv — there's no per-host virtualenv split.

## 2. Install the Streamlit component package (editable)

The Streamlit host imports `chat_tree_canvas_st`, which lives in
`packages/chat-tree-canvas-streamlit/` and is **not** in requirements.txt
(it's a local editable install, not a PyPI dependency):

```
pip install -e packages/chat-tree-canvas-streamlit
```

Skip this if you only intend to run `app_rx`.

## 3. Environment variables

Copy `.env.example` to `.env` at the repo root and fill in what you need
(`.env` itself is gitignored):

```
CHAT_API_USER_ID=<your dev user id, e.g. your email>

# Auth0 (optional — omit entirely to run app_rx/app_st with a dev-fallback
# identity instead of a real login flow)
AUTH0_DOMAIN=<your-tenant>.auth0.com
AUTH0_CLIENT_ID=...
AUTH0_CLIENT_SECRET=...
ADMIN_ID_TOKEN=...
REFLEX_FE_BASE_URL=http://localhost:3000

# true = rerunning a node cascades to all descendants (BFS); false = only
# leaf nodes can be rerun/duplicated. Read once at process startup.
CASCADING_RERUNS=true

# Chat backend selection: "reference" is the only built-in provider —
# the minimal protocol in docs/PROVIDERS.md, served by mock_provider.py
# or insecure_agent_provider.py on :9000 (see step 6 below).
CHAT_API_PROVIDER=reference
```

Both `app_rx` and `app_st` load this same root `.env` file.

## 4. Building the shared React canvas package

`packages/chat-tree-canvas-react/` is raw JSX with no build step of its own
— it's consumed differently by each host:

- **app_rx (Reflex)**: consumed via a **bun link junction** so edits are
  live under the dev server, no rebuild needed. `run.cmd` registers this
  link automatically (idempotent, one-time per machine):
  ```
  run.cmd
  ```
  which internally does `bun link` inside `packages/chat-tree-canvas-react`
  before `reflex run`. If you run `reflex run` directly instead of
  `run.cmd`, do that link step yourself once:
  ```
  "%LocalAppData%\reflex\bun\bin\bun.exe" link
  ```
  (run from inside `packages/chat-tree-canvas-react`).

- **app_st (Streamlit)**: the React package is bundled into a **static,
  gitignored** JS/CSS bundle via Vite — there is no live-reload here. See
  step 5.

## 5. Building the Streamlit component frontend

Required once per machine, and again **every time
`packages/chat-tree-canvas-react/src/*` or
`packages/chat-tree-canvas-streamlit/frontend/src/*` changes** — the built
output (`chat_tree_canvas_st/static/`) is gitignored, not committed:

```
cd packages\chat-tree-canvas-streamlit\frontend
npm install
npm run build
```

This produces `chat_tree_canvas_st/static/index-<hash>.js` and
`style-<hash>.css`. Skip this whole step if you only intend to run
`app_rx`.

> **Picking up a rebuild requires a full process restart, not just a
> browser refresh.** `chat_tree_canvas_st/__init__.py` resolves the
> `index-*.js` / `style-*.css` glob **once** into a module-level global the
> first time the component renders, and never re-resolves it for the life
> of the process. A page refresh only reruns the Streamlit *script*, not
> the *process* — stop and restart `streamlit run app_st/app.py` after every
> `npm run build`.

## 6. Run a chat backend

Both hosts need something listening on `CHAT_API_BASE_URL` (default
`http://localhost:9000`). Two options ship in this repo:

```
uvicorn mock_provider:app --port 9000            # canned responses, zero setup
# or
uvicorn insecure_agent_provider:app --port 9000  # a real, LLM-backed agent — see README.md
```

See `docs/PROVIDERS.md` for the protocol they implement and
`docs/RENDERERS.md` for the response-block renderer contract.

## 7. Running app_rx (Reflex)

```
run.cmd
```

or equivalently:

```
.venv\Scripts\activate
reflex run
```

Open **http://localhost:3000**. Reflex's own websocket backend runs on
**8001** (`rxconfig.py`).

On Windows, if running `reflex run` from a non-interactive shell, set
`PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8` first — otherwise it crashes with
`UnicodeEncodeError` on the startup spinner glyph.

## 8. Running app_st (Streamlit)

```
.venv\Scripts\activate
streamlit run app_st/app.py
```

Serves on **http://localhost:8765** (pinned in `.streamlit/config.toml` —
do not run with `--server.port` pointing anywhere else unless you also
update the Auth0 redirect URLs below, since Auth0's OAuth callback is built
from the *configured* `redirect_uri` regardless of which port the server
actually binds to).

### Auth0 setup for app_st (optional)

Only needed if you want to test the real login flow instead of the
dev-fallback identity:

1. Copy `.streamlit/secrets.toml.example` → `.streamlit/secrets.toml` and
   fill in `client_id` / `client_secret` / `server_metadata_url` from the
   same Auth0 tenant `.env` uses, plus a freshly generated `cookie_secret`:
   ```
   python -c "import secrets; print(secrets.token_hex(32))"
   ```
2. In the Auth0 console, on that Application, add:
   - Allowed Callback URLs: `http://localhost:8765/oauth2callback`
   - Allowed Logout URLs: `http://localhost:8765`
   - Token Endpoint Authentication Method: `Post`

`.streamlit/secrets.toml` is gitignored — never commit it.

## 9. Data & sessions

Both hosts read/write the **same** SQLite file, `.db/canvas.db` (dot-prefixed
on purpose — Reflex's dev-mode file watcher skips hidden directories; a
visible data path would trigger a hot-reload + state reset on every save).
Loading the same `session_id` in both hosts shows the same tree live.
Never move this to a visible directory.

## Running only app_rx

Do steps 0, 1, 3, 4 (bun-link part only), 7. Skip 2, 5, 8.

## Running only app_st

Do steps 0, 1, 2, 3, 5, 8. Skip step 4's bun-link (harmless if `run.cmd`
still does it, but not required).

## Troubleshooting

- **`ModuleNotFoundError: No module named 'app_st'`** — you ran
  `streamlit run` from somewhere that isn't a fully synced checkout of
  `app_st/app.py`'s repo-root `sys.path` fix. This is already handled in
  `app_st/app.py` (inserts the repo root onto `sys.path` before any
  repo-local import) — if you still hit this, confirm you're running the
  `app_st/app.py` from this repo, not a stale copy.
- **Streamlit changes to the canvas frontend "have no effect"** — you
  rebuilt (`npm run build`) but only refreshed the browser. Restart the
  `streamlit run` process (see step 5's callout).
- **Vite fails to resolve `reactflow`/`plotly.js-dist-min`/etc. on a fresh
  `.web/`** — this is handled by a Reflex plugin in `rxconfig.py`
  (`optimizeDeps.include` injection for the bun-linked package's deps); if
  you deleted `.web/` and still see import-analysis errors, confirm
  `rxconfig.py` wasn't modified to remove that patch.
- **`bun link` fails with a permissions error** — this is a known Windows
  issue with bun's `file:` protocol; `run.cmd`'s `bun link` (junction-based)
  avoids it. Don't hand-edit `package.json` to use `file:` links directly.
