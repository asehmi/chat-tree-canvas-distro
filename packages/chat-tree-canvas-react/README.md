# chat-tree-canvas-react

A branching chat-tree canvas as a plain React component. Conversations are a
**tree of nodes** on an infinite ReactFlow canvas; each node is one
prompt/answer exchange whose answer is a list of **typed blocks** (markdown
text, tool status, generated scripts, metrics, tables, Plotly charts, mermaid
diagrams) drawn by a pluggable renderer registry.

This package contains **no server logic and no framework bindings** — it is a
controlled component. The host owns the tree state and all behavior; the
canvas renders what it's given and reports user intent through callbacks. The
reference host is the [chat-tree-canvas](../../README.md) Reflex app; any
React app can host it the same way.

## Install

Not published to npm — consume it as a local dependency. With npm/pnpm the
`file:` protocol works directly:

```jsonc
// package.json
"dependencies": {
  "chat-tree-canvas-react": "file:../packages/chat-tree-canvas-react"
}
```

With **bun on Windows**, `file:` copies can fail with EPERM — use bun's link
protocol instead (this is what the Reflex host app does):

```sh
cd packages/chat-tree-canvas-react && bun link          # once per machine
# then in your app:
bun add "chat-tree-canvas-react@link:chat-tree-canvas-react"
```

**Linked-dependency caveat:** package managers don't install a linked
package's own dependencies. When consuming via `link:` (or a plain
symlink/junction), your app must also depend on `reactflow`,
`react-markdown`, `remark-gfm`, and `plotly.js-dist-min` at matching
versions. A real `file:`/registry install pulls them transitively.

Ships **raw JSX** (`main: src/index.jsx`, no build step). Vite/esbuild hosts
consume it as-is; a webpack host must include the package in its JSX
transpilation (e.g. babel-loader `include`).

Peer deps: `react` / `react-dom` ≥ 18. Regular deps (installed
transitively): `reactflow@11` (v12 was renamed `@xyflow/react` — don't blind
upgrade), `react-markdown`, `remark-gfm`, `plotly.js-dist-min` (the bundle
heavyweight — see Slimming below).

## Quickstart

```jsx
import { useState } from 'react';
import ChatCanvas from 'chat-tree-canvas-react';
import 'reactflow/dist/style.css';           // required — ReactFlow base styles
import 'chat-tree-canvas-react/theme.css';   // or define --highlight yourself

function App() {
  const [nodes, setNodes] = useState([initialNode]);
  const [edges, setEdges] = useState([]);

  return (
    <div style={{ width: '100vw', height: '100vh' }}>
      <ChatCanvas
        nodes={nodes}
        edges={edges}
        mode="dark"                          // "light" | "dark"
        cascadingReruns={true}               // rerun/duplicate on all nodes vs leaves only
        onSubmitPrompt={(id, prompt) => streamAnswerInto(id, prompt)}
        onBranchNode={(id) => addChildUnder(id)}
        onBranchFromText={(id, text) => addChildUnder(id, `Regarding "${text}": `)}
        onDuplicateNode={(id) => duplicateAsSibling(id)}
        onClearNode={(id) => rerunSubtree(id)}
        onDeleteNode={(id) => deleteSubtree(id)}
        onAddRoot={(x, y) => addRootAt(x, y)}
        onNodeMoved={(id, x, y) => persistPosition(id, x, y)}
        onNodeResized={(id, x, y, w, h) => persistGeometry(id, x, y, w, h)}
        onConnectEdge={(source, target) => validateAndLink(source, target)}
        onEdgeReconnect={(edgeId, source, target) => revalidateLink(edgeId, source, target)}
        onEdgeDelete={(edgeId) => detach(edgeId)}
      />
    </div>
  );
}
```

The component re-renders from `nodes`/`edges` on every change — stream an
answer by repeatedly updating the node's `data.blocks` and re-passing the
array. Incoming props are treated as server truth, with one exception: a node
the user is actively dragging or resizing is never yanked out from under the
cursor.

## Data contract

### Node shape (ReactFlow node + `data`)

```jsonc
{
  "id": "node-uuid",
  "type": "chat",                     // always "chat"
  "position": {"x": 0, "y": 0},
  "data": {
    "prompt": "",                     // "" + status "idle" → editable textarea
    "status": "idle",                 // idle | streaming | complete | error
    "blocks": [],                     // typed blocks, see below
    "error": "",                      // shown in a red box when non-empty
    "route": "",                      // header intent badge (e.g. backend route)
    "leaf": true,                     // host-maintained; gates buttons when cascadingReruns=false
    "width": 0,                       // 0 = default (380); set from onNodeResized
    "height": 0                       // 0 = auto height
  }
}
```

Edges are plain `{id, source, target}`. The canvas draws whatever edges it is
given — cycle/single-parent validation is the host's job (do it in
`onConnectEdge`/`onEdgeReconnect` and simply don't add invalid links).

### Block shapes (dispatched on `kind`)

```jsonc
{"kind": "text",   "text": "markdown (GFM)"}
{"kind": "tool",   "tool": "name", "status": "called|ok|denied", "reason": ""}
{"kind": "script", "code": "print('hi')"}
{"kind": "metric", "label": "Latency", "value": 42, "unit": "ms"}
{"kind": "table",  "title": "Results", "records": [{"col": "val"}]}
{"kind": "chart",  "title": "Trend", "figure": "<Plotly figure JSON string>"}
{"kind": "mermaid", "title": "Flow", "code": "graph TD; A-->B"}
{"kind": "question", "text": "What's your risk tolerance?"}
```

Unknown kinds are silently skipped (forward compatibility). Add custom kinds
by extending `RENDERERS`:

```jsx
import { RENDERERS } from 'chat-tree-canvas-react';
import { GaugeBlock } from './GaugeBlock.jsx';
RENDERERS.gauge = GaugeBlock;   // before first render
```

A renderer receives `{ block }`, reads the active palette from `ThemeCtx`,
and may use `useWidthScaledHeight(ref, ratio, min, max)` for content that
should grow as the node is resized (both exported). Full authoring guide:
`docs/RENDERERS.md` in the parent repo.

### Callbacks

All optional; omit what your host doesn't support and the corresponding UI
simply does nothing.

| Prop | Fires when |
|---|---|
| `onSubmitPrompt(id, prompt)` | Send clicked / Ctrl+Enter in an idle node |
| `onBranchNode(id)` | ✚ header button or Ctrl/⌘+B on the selected node |
| `onBranchFromText(id, text)` | "Branch from selection" chip on selected answer text |
| `onDuplicateNode(id)` | ⧉ header button |
| `onClearNode(id)` | ⟲ header button (host decides: clear, rerun, cascade…) |
| `onDeleteNode(id)` | ✕ header button or Delete/Backspace on the selected node |
| `onAddRoot(x, y)` | double-click on empty canvas (flow coordinates) |
| `onNodeMoved(id, x, y)` | drag ended |
| `onNodeResized(id, x, y, width, height)` | resize handle released |
| `onConnectEdge(source, target)` | new edge dragged between handles |
| `onEdgeReconnect(edgeId, source, target)` | existing edge end dropped on another handle |
| `onEdgeDelete(edgeId)` | edge end dropped on empty canvas |

## Theming

Two layers:

1. **`mode` prop** (`"light"`/`"dark"`) selects a full palette
   (`PALETTES` in `src/renderers/theme.jsx`) carried to every node and block
   via `ThemeCtx` — context on purpose, because context updates pierce
   ReactFlow's node memoization, so a toggle re-skins live.
2. **CSS variables** — a handful of accents (`var(--ctc-highlight)`: header
   icons, dark-mode node frames and scrollbars, intent badge) resolve from
   CSS so they track the host theme without re-rendering. The `--ctc-`
   prefix is deliberate: this package expects to be dropped into a host
   that already has its own `--primary`/`--highlight`/etc.-style tokens, so
   its public variable names are namespaced to avoid colliding with them.
   Import `chat-tree-canvas-react/theme.css` for defaults (add a `dark`
   class on an ancestor for dark values), or alias `--ctc-highlight` (and
   the rest of the set — see `theme.css`) onto your own theme's tokens.

## Slimming the bundle

`plotly.js-dist-min` (~1 MB gz) is only needed for `chart` blocks. A host
that never renders charts can drop it:

```jsx
import { RENDERERS } from 'chat-tree-canvas-react';
delete RENDERERS.chart;   // then exclude plotly from the bundle / lazy-load it
```

`mermaid` (~2 MB gz across its own code-split diagram-type chunks — bigger
than plotly) is only needed for `mermaid` blocks, and only the specific
diagram-type chunk(s) actually rendered load at runtime (mermaid dynamically
imports per diagram type). A host that never renders mermaid diagrams can
drop it the same way:

```jsx
import { RENDERERS } from 'chat-tree-canvas-react';
delete RENDERERS.mermaid;   // then exclude mermaid from the bundle / lazy-load it
```

## Gotchas

- One `window` keydown listener is registered per mounted canvas (for
  Ctrl+B / Delete shortcuts); fine for one canvas per page.
- `nodes` prop changes are diffed via `JSON.stringify` — pass new arrays,
  don't mutate in place.
- Wrap the canvas in a sized container; it fills 100% of its parent.
