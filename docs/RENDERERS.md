# Building a custom block renderer

Every node response on the canvas is a list of **typed blocks**
(`data.blocks`), and each block is drawn by a small React component in
`packages/chat-tree-canvas-react/src/renderers/` — one file per kind, dispatched on `block.kind`:

```
packages/chat-tree-canvas-react/src/renderers/
├── index.jsx               RENDERERS registry + ResponseBlock dispatcher
├── theme.jsx               PALETTES + ThemeCtx (light/dark palette context)
├── useWidthScaledHeight.js size-aware content hook
├── useMermaidSvg.js        mermaid.render() hook — the one call site, shared below
├── MermaidDiagram.jsx      presentational mermaid shell (scroll box, source
│                           expander, fail-soft) — not a kind itself, shared by
│                           MermaidBlock AND TextBlock's inline fence override
├── TextBlock.jsx           {"kind":"text",   "text": md} — a ```mermaid fence
│                           inside the markdown renders via MermaidDiagram too
├── ToolBlock.jsx           {"kind":"tool",   "tool", "status", "reason"}
├── ScriptBlock.jsx         {"kind":"script", "code"}
├── MetricBlock.jsx         {"kind":"metric", "label", "value", "unit"}
├── TableBlock.jsx          {"kind":"table",  "title", "records": [dict]}
├── ChartBlock.jsx          {"kind":"chart",  "title", "figure": plotly-json}
├── MermaidBlock.jsx        {"kind":"mermaid", "title", "code": mermaid source}
│                           — thin adapter over MermaidDiagram
└── QuestionBlock.jsx       {"kind":"question", "text"} — see the nodeId note below
```

Unknown kinds are silently skipped, so a backend emitting a new kind
degrades gracefully on a front end that doesn't know it yet.

**A block kind isn't the only way content reaches a renderer.** `mermaid`
diagrams also arrive *inline*, inside an ordinary `text` block's markdown, as
a ` ```mermaid ` fenced code block — a backend whose free-text response mode
can't emit a structured event mid-answer can still include a diagram this
way, as plain markdown content rather than a turn-level signal.
`TextBlock.jsx` detects the fence via `react-markdown`'s `components.code`
override and renders it through the same `MermaidDiagram` component the
structured `mermaid` block uses, so a diagram looks identical either way it
arrived. If you're adding a kind that could plausibly show up embedded in
prose the same way, this is the pattern to follow: put the actual rendering
in a shared, non-dispatched component, and call it from both the block
renderer and `TextBlock.jsx`'s markdown override.

## How a block comes to exist (the full pipeline)

```
provider stream event ──► apply_event_to_blocks()          ──► data.blocks ──► ResponseBlock
(docs/PROVIDERS.md)        (chat_tree/blocks.py,          (Reflex state)   (renderers/index.jsx)
                            also replayed from history rows)
```

A renderer only draws; the **Python reducer** decides which events become
which blocks. That means a brand-new kind touches three places:

1. **Provider** emits the event (usually `tool_call_result` with your kind).
2. **Reducer** (`apply_event_to_blocks` in `chat_tree/blocks.py`)
   maps the event to a block dict. Because history hydration replays the
   same events through the same reducer, persistence works automatically.
3. **Renderer** draws the block (this guide).

If you can express your content as one of the existing kinds (markdown,
metric, table, chart), you only touch the provider — steps 2 and 3 are done.

## Step-by-step: a new `gauge` kind

### 1. Emit it from your provider

```python
yield {"type": "tool_call_result",
       "data": {"kind": "gauge", "label": "CPU", "value": 0.72, "max": 1.0}}
```

### 2. Fold it into a block (Python reducer)

In `apply_event_to_blocks` (`app_rx/states/canvas_state.py`), inside the
`tool_call_result` branch:

```python
elif kind == "gauge":
    blocks.append({
        "kind": "gauge",
        "label": data.get("label", ""),
        "value": data.get("value", 0),
        "max": data.get("max", 1),
    })
```

Keep blocks JSON-serializable (they live in Reflex state and in exports).

### 3. Write the renderer

`packages/chat-tree-canvas-react/src/renderers/GaugeBlock.jsx`:

```jsx
// {"kind": "gauge", "label": str, "value": number, "max": number}
import React, { useContext, useRef } from 'react';
import { ThemeCtx } from './theme.jsx';
import { useWidthScaledHeight } from './useWidthScaledHeight.js';

export function GaugeBlock({ block }) {
  const C = useContext(ThemeCtx);              // active light/dark palette
  const wrapRef = useRef(null);                // optional: size-aware height
  const height = useWidthScaledHeight(wrapRef, 0.2, 40, 120);
  const frac = Math.min(1, Math.max(0, block.value / (block.max || 1)));
  return (
    <div ref={wrapRef} style={{ margin: '6px 0' }}>
      <div style={{ fontSize: 10, color: C.textDim }}>{block.label}</div>
      <div style={{ height, background: C.codeBg, borderRadius: 6,
                    border: `1px solid ${C.nodeBorder}`, overflow: 'hidden' }}>
        <div style={{ width: `${frac * 100}%`, height: '100%',
                      background: 'var(--ctc-highlight)' }} />
      </div>
    </div>
  );
}

export default GaugeBlock;
```

### 4. Register it

In `packages/chat-tree-canvas-react/src/renderers/index.jsx`:

```jsx
import GaugeBlock from './GaugeBlock.jsx';

export const RENDERERS = {
  // …existing kinds…
  gauge: GaugeBlock,
};
```

Done — streaming, hydration after reload, and cascading reruns all render
your kind, because they all flow through the same reducer + registry.

## Renderer conventions

**Props.** A renderer receives `{ block, nodeId }` — the dict your reducer built,
plus the id of the node it's rendered inside. `block` is the one every kind
actually uses (treat it as read-only); `nodeId` exists ONLY for a renderer
that needs to fire a node-scoped callback back up through `CanvasApi` (see
`QuestionBlock.jsx` — it calls `api.answerQuestion(nodeId, ...)` on submit).
None of the presentational kinds (text/tool/script/metric/table/chart) use
it; don't destructure it unless you need it.

`CanvasApi` itself lives in `renderers/canvasApi.jsx`, not in `ChatCanvas.jsx`
— `ChatCanvas.jsx` already imports from `renderers/index.jsx`, so a renderer
importing the context back from `ChatCanvas.jsx` would be circular. Add any
new node-scoped callback to the `api` object built in `ChatCanvas.jsx` (the
top-level component, not `CanvasInner`) and to the prop list on the host
side (both `chat_canvas.py`'s `EventHandler`s for app_rx and `index.jsx`'s
`CALLBACKS` array for app_st) — `onAnswerQuestion`/`answerQuestion` is the
worked example.

**Theming.** Never hard-code colors. Two sources:
- `useContext(ThemeCtx)` → the active palette object (see `theme.jsx` for
  every key: `text`, `textDim`, `codeBg`, `codeBorder`, `nodeBorder`,
  `accent`, `error`, …, plus a plain `mode` (`"light"`/`"dark"`) and `isDark`
  boolean for the rare renderer that needs light/dark as a flag rather than
  CSS values — e.g. a third-party library like mermaid that bakes colors
  into its own output at render time instead of reading CSS variables). It
  re-renders your component on mode toggle.
- The package's own namespaced CSS variables (`var(--ctc-highlight)`,
  `var(--ctc-primary)`, …) resolve live from the body theme classes — fine
  in inline styles. Namespaced with a `--ctc-` prefix, not the host's own
  `--highlight`/`--primary`/etc., so the package's contract can't collide
  with a host's pre-existing tokens of the same generic shape (a host aliases
  its own tokens onto these, e.g. `assets/css/themes.css`'s
  `--ctc-highlight: var(--highlight);`).

**Sizing.** Node bodies scroll; your block decides how much height to claim.
For content that benefits from a bigger node, use
`useWidthScaledHeight(ref, ratio, min, max)` — it tracks the node's resize
handles live via ResizeObserver (ChartBlock uses ratio 0.62 for ~5:3;
TableBlock/ScriptBlock use it for their max-height caps).

**Interaction classes** (ReactFlow interplay — easy to forget):
- `className="nodrag"` on anything clickable/selectable, or ReactFlow will
  start dragging the node on mousedown.
- `className="nowheel"` on anything that scrolls internally, or the canvas
  zooms instead of your content scrolling.
- `className="ctc-scroll"` for the themed thin scrollbars.

**Markdown.** For markdown content reuse `<div className="ctc-md">` +
`react-markdown` (see `TextBlock.jsx`) — the `.ctc-md` styles are generated
per-palette in the package's `ChatCanvas.jsx`.

**Hooks discipline.** Call all hooks before any early return (React rule) —
see `TableBlock.jsx`/`ChartBlock.jsx` for the pattern.

**Fail soft.** Bad data should render a small inline notice, not throw —
`ChartBlock`'s invalid-JSON branch is the model. A crash here takes down the
whole canvas.

## Testing your renderer

1. Quickest loop: add your kind to `_mock_events` in `mock_provider.py`,
   then run the canvas against it (`CHAT_API_PROVIDER=reference`,
   `uvicorn mock_provider:app --port 9000`). Submit a node and watch your
   block stream in; reload the page to confirm hydration replays it.
2. Check both modes with the menu's light/dark toggle.
3. Resize the node (corner handle) to verify your sizing behavior.

## Dev-server notes

The canvas lives in the standalone `packages/chat-tree-canvas-react` package,
junction-linked into `.web/node_modules` via bun's link protocol (`run.cmd`
registers the link; see the package README). Because it's a junction, **edits
to renderer files are live immediately** — just refresh the browser; no sync
step. On a fresh machine, run `bun link` once inside the package directory
(or just use `run.cmd`, which does it for you).
