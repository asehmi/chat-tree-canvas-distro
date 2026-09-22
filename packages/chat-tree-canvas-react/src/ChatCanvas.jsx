// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// ChatCanvas — self-contained ReactFlow canvas of branching chat nodes.
// Port of local-lmcanvas's Canvas/CustomNode to a single Reflex-friendly
// component: node/edge data and all mutations live in Python state; this
// component owns only transient UI state (dragging, textarea text, text
// selection) and reports intents up through Reflex event-handler props:
//   onSubmitPrompt(id, prompt), onBranchNode(id), onBranchFromText(id, text),
//   onDuplicateNode(id), onClearNode(id), onDeleteNode(id),
//   onAddRoot(x, y), onNodeMoved(id, x, y), onNodeResized(id, x, y, w, h),
//   onConnectEdge(source, target), onEdgeReconnect(edgeId, source, target),
//   onEdgeDelete(edgeId)
//
// Theming: the `mode` prop ("light" | "dark") selects a palette (renderers/
// theme.jsx); it travels through ThemeCtx so every node/block re-renders on
// toggle (context pierces ReactFlow's memo).
//
// Responses are typed block lists (data.blocks) rendered by the renderer
// namespace (renderers/index.jsx) — one file per block kind, dispatched on
// block.kind: text / tool / script / metric / table / chart.
import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import ReactFlow, {
  applyNodeChanges,
  Background,
  Controls,
  MiniMap,
  Handle,
  NodeResizeControl,
  Position,
  ReactFlowProvider,
  useReactFlow,
} from 'reactflow';
// NOTE: hosts must import 'reactflow/dist/style.css' themselves (plus,
// optionally, this package's theme.css). CSS subpath imports don't resolve
// from a linked package's real path under Vite, and CSS loading is
// host-bundler business anyway. See README.md → Quickstart.

import { ResponseBlock } from './renderers/index.jsx';
import { PALETTES, ThemeCtx } from './renderers/theme.jsx';
import { CanvasApi } from './renderers/canvasApi.jsx';

// Provided by CanvasInner (inside the ReactFlow tree) so ChatNode's resize
// control can guard the props-sync effect and report final geometry.
const ResizeApi = createContext({ start: () => {}, end: () => {} });
// Provided by CanvasInner: Map<parentId, childNode[]>, used by ChatNode to
// derive whether a `question` block already has an answering child (see the
// display-block substitution in ChatNode) — see docs/RENDERERS.md.
const TreeApi = createContext(new Map());

const NODE_WIDTH = 380;

// ── Chat node ────────────────────────────────────────────────

// Header action buttons: larger bold glyphs in the theme highlight color.
const iconBtnStyle = (C) => ({
  background: 'transparent',
  border: `1px solid ${C.nodeBorder}`,
  borderRadius: 6,
  color: C.icon,
  cursor: 'pointer',
  fontSize: 16,
  fontWeight: 700,
  lineHeight: 1,
  padding: '4px 8px',
  fontFamily: 'inherit',
});

const handleStyle = (C) => ({
  background: C.handle,
  width: 10,
  height: 10,
  border: `2px solid ${C.bg}`,
});

// Text/table/chart blocks → plain text, for the copy-response button.
// Mirrors chat_tree/blocks.py's answer_text (tables/charts redacted to a
// note) so "copy" matches what chat_tree.context would send as this
// node's contribution to a descendant's historical-context postfix.
function answerText(blocks) {
  const parts = [];
  for (const block of blocks) {
    if (block.kind === 'text' && block.text && block.text.trim()) {
      parts.push(block.text.trim());
    } else if (block.kind === 'table') {
      const records = block.records || [];
      const cols = records.length
        ? Object.keys(records[0]).join(', ')
        : 'none';
      parts.push(
        `[table "${block.title || 'untitled'}" removed from context — columns: ${cols}]`,
      );
    } else if (block.kind === 'chart') {
      parts.push(`[chart "${block.title || 'untitled'}" removed from context]`);
    } else if (block.kind === 'mermaid') {
      parts.push(`[mermaid diagram "${block.title || 'untitled'}" removed from context]`);
    } else if (block.kind === 'question' && block.text && block.text.trim()) {
      parts.push(`[Agent asked: "${block.text.trim()}"]`);
    }
  }
  return parts.join('\n').trim();
}

// window.getSelection() retargets anchorNode to the shadow HOST element when
// the selection lives inside a shadow root (app_st mounts this component in
// a real shadow root; app_st's isolate_styles=True — see chat_tree_canvas_st).
// That breaks responseRef.contains(sel.anchorNode) below. Asking the node's
// own root for its selection keeps anchorNode inside the actual tree.
// Chromium implements ShadowRoot.getSelection(); Firefox/Safari don't yet,
// so branch-from-selection in app_st is Chromium-only until they do.
function getSelectionFor(node) {
  const root = node?.getRootNode?.();
  if (root && typeof root.getSelection === 'function') return root.getSelection();
  return window.getSelection();
}

function ChatNode({ id, data, selected }) {
  const api = useContext(CanvasApi);
  const C = useContext(ThemeCtx);
  const resize = useContext(ResizeApi);
  const childrenByParent = useContext(TreeApi);
  const [prompt, setPrompt] = useState(data.prompt || '');
  // Text selected inside this node's response — offers "branch from selection".
  const [selText, setSelText] = useState('');
  const responseRef = useRef(null);
  // Brief checkmark feedback after a successful copy-response click.
  const [copied, setCopied] = useState(false);

  const blocks = data.blocks || [];
  const streaming = data.status === 'streaming';
  const editing = data.status === 'idle';
  const fixedHeight = Boolean(data.height);
  const iconBtn = iconBtnStyle(C);

  // A `question` block only ever renders its interactive (unanswered) form
  // (QuestionBlock) — once this node has a child whose prompt was composed
  // to answer it (compose_followup_prompt in chat_tree/blocks.py), swap in
  // a plain text block showing that composed line instead. Derived from
  // tree structure (durable, survives reload) rather than a stored mutation
  // (blocks are always reconstructed by replaying backend events — a
  // one-off in-session mutation wouldn't survive it). If the parent is
  // later rerun and stops asking the same question, this naturally reverts
  // to unanswered — expected staleness, not a bug (same characteristic
  // branch-from-selection already has).
  const children = childrenByParent.get(id) || [];
  const displayBlocks = blocks.map((b) => {
    if (b.kind !== 'question') return b;
    const answered = children.find((c) =>
      c?.data?.prompt?.startsWith(`Agent asked: ${b.text}. User responded with:`),
    );
    return answered ? { kind: 'text', text: answered.data.prompt } : b;
  });

  // A rerun clears an existing (possibly tall) answer down to nothing right
  // as streaming starts, collapsing the node for one frame before it grows
  // back — jarring on its own, and the sudden height jump seemed to be
  // what was confusing ReactFlow's background/viewport rendering on a
  // rerun start (see _pm/LIVE_TOKEN_STREAMING_IN_APP_ST.md). Keep tracking
  // the response area's last "settled" height whenever it ISN'T streaming;
  // the moment blocks clears to empty for a fresh stream, floor the area at
  // that last-known height instead of letting it collapse to the "…"
  // placeholder's natural (tiny) size. Once real content arrives it grows
  // past the floor normally.
  const settledHeightRef = useRef(0);
  useLayoutEffect(() => {
    if (!streaming && responseRef.current) {
      settledHeightRef.current = responseRef.current.offsetHeight;
    }
  });

  // Follow the stream: keep the response area pinned to the bottom.
  useEffect(() => {
    if (streaming && responseRef.current) {
      responseRef.current.scrollTop = responseRef.current.scrollHeight;
    }
  }, [displayBlocks, streaming]);

  const submit = useCallback(() => {
    const text = prompt.trim();
    if (text) api.submit?.(id, text);
  }, [api, id, prompt]);

  const onKeyDown = useCallback(
    (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        submit();
      }
    },
    [submit],
  );

  const copyResponse = useCallback(() => {
    const text = answerText(displayBlocks);
    if (!text) return;
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {}); // insecure context / permission denied — fail silent
  }, [displayBlocks]);

  // Capture a text selection made inside the response area (LMCanvas-style
  // text branching). Cleared whenever the document selection collapses.
  const onResponseMouseUp = useCallback(() => {
    const sel = getSelectionFor(responseRef.current);
    const text = sel ? sel.toString().trim() : '';
    if (
      text &&
      sel.rangeCount > 0 &&
      responseRef.current &&
      responseRef.current.contains(sel.anchorNode)
    ) {
      setSelText(text);
    } else {
      setSelText('');
    }
  }, []);

  useEffect(() => {
    if (!selText) return;
    const root = responseRef.current?.getRootNode?.() || document;
    const onSelChange = () => {
      const sel = getSelectionFor(responseRef.current);
      if (!sel || !sel.toString().trim()) setSelText('');
    };
    root.addEventListener('selectionchange', onSelChange);
    return () => root.removeEventListener('selectionchange', onSelChange);
  }, [selText]);

  const border = data.error
    ? C.error
    : streaming || selected
      ? C.nodeBorderSelected
      : C.nodeBorderOuter;

  return (
    <div
      style={{
        position: 'relative',
        width: '100%',
        height: fixedHeight ? '100%' : 'auto',
        display: 'flex',
        flexDirection: 'column',
        boxSizing: 'border-box',
        background: C.node,
        border: `1px solid ${border}`,
        borderRadius: 10,
        boxShadow: selected
          ? `0 0 0 1px ${C.nodeBorderSelected}, ${C.shadowSelected}`
          : C.shadow,
        color: C.text,
        fontFamily:
          'ui-sans-serif, system-ui, -apple-system, Segoe UI, sans-serif',
        fontSize: 13,
        animation: streaming ? 'ctc-pulse 1.6s ease-in-out infinite' : 'none',
      }}
    >
      {(() => {
        const resizeProps = {
          minWidth: 300,
          minHeight: 160,
          onResizeStart: () => resize.start(id),
          onResizeEnd: (_e, params) => {
            const ok =
              params &&
              [params.x, params.y, params.width, params.height].every(
                Number.isFinite,
              );
            resize.end(id, ok ? params : null);
          },
        };
        return (
          <>
            <NodeResizeControl
              {...resizeProps}
              variant="line"
              position="right"
              style={{ borderColor: 'transparent', width: 8 }}
            />
            <NodeResizeControl
              {...resizeProps}
              variant="line"
              position="bottom"
              style={{ borderColor: 'transparent', height: 8 }}
            />
            <NodeResizeControl
              {...resizeProps}
              position="bottom-right"
              style={{ background: 'transparent', border: 'none' }}
            >
              <div
                title="Drag to resize"
                style={{
                  position: 'absolute',
                  bottom: 2,
                  right: 2,
                  width: 18,
                  height: 18,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  background: C.resizeBg,
                  border: `1px solid ${C.nodeBorder}`,
                  borderRadius: 5,
                  cursor: 'nwse-resize',
                  color: C.icon,
                  fontSize: 11,
                  lineHeight: 1,
                  userSelect: 'none',
                }}
              >
                ◢
              </div>
            </NodeResizeControl>
          </>
        );
      })()}
      <Handle type="target" position={Position.Top} style={handleStyle(C)} />

      {/* Header: status + actions */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '8px 12px',
          borderBottom: `1px solid ${C.nodeBorder}`,
        }}
      >
        {data.route ? (
          <span
            style={{
              fontSize: 9,
              fontWeight: 700,
              letterSpacing: 0.8,
              textTransform: 'uppercase',
              color: 'var(--ctc-highlight)',
              border: '1px solid color-mix(in srgb, var(--ctc-highlight) 35%, transparent)',
              borderRadius: 4,
              padding: '1px 5px',
            }}
          >
            {data.route}
          </span>
        ) : (
          <span style={{ fontSize: 10, color: C.textDim }}>
            {editing ? 'new prompt' : ''}
          </span>
        )}
        {streaming && (
          <span style={{ fontSize: 10, color: C.accent }}>thinking…</span>
        )}
        <div style={{ flex: 1 }} />
        {data.status === 'complete' && (
          <button
            className="nodrag"
            style={iconBtn}
            title="Add: a new node at the next level"
            onClick={() => api.branch?.(id)}
          >
            ✚
          </button>
        )}
        {/* Duplicate/rerun visibility is controlled by the CASCADING_RERUNS
            env var (api.cascading, passed down from Python). The original
            leaf-only gate, kept for reference:
              {data.leaf && (data.status === 'complete' || data.status === 'error') && (…)}
        */}
        {(api.cascading || data.leaf) &&
          (data.status === 'complete' || data.status === 'error') && (
          <button
            className="nodrag"
            style={iconBtn}
            title="Duplicate: copy this prompt into a new node (fresh conversation) under the same parent"
            onClick={() => api.duplicate?.(id)}
          >
            ⧉
          </button>
        )}
        {(api.cascading || data.leaf) &&
          (data.status === 'complete' || data.status === 'error') && (
          <button
            className="nodrag"
            style={iconBtn}
            title="Rerun: delete this response and rerun the prompt here and in all descendant nodes"
            onClick={() => api.clear?.(id)}
          >
            ⟲
          </button>
        )}
        {!streaming && blocks.length > 0 && (
          <button
            className="nodrag"
            style={iconBtn}
            title={copied ? 'Copied!' : 'Copy response to clipboard'}
            onClick={copyResponse}
          >
            {copied ? '✓' : '⎘'}
          </button>
        )}
        <button
          className="nodrag"
          style={iconBtn}
          title="Delete this node and its descendants"
          onClick={() => api.del?.(id)}
        >
          ✕
        </button>
      </div>

      {/* Prompt: editable until first submit, static afterwards */}
      {editing ? (
        <div style={{ padding: 10 }}>
          <textarea
            className="nodrag nowheel"
            autoFocus
            value={prompt}
            placeholder="Ask something…  (Ctrl+Enter to send)"
            onChange={(e) => setPrompt(e.target.value)}
            onKeyDown={onKeyDown}
            rows={3}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              resize: 'vertical',
              background: C.inputBg,
              border: `1px solid ${C.nodeBorder}`,
              borderRadius: 8,
              color: C.text,
              fontFamily: 'inherit',
              fontSize: 13,
              lineHeight: 1.45,
              padding: 8,
              outline: 'none',
            }}
          />
          <div
            style={{
              display: 'flex',
              justifyContent: 'flex-end',
              marginTop: 6,
            }}
          >
            <button
              className="nodrag"
              style={{
                background: C.accent,
                border: 'none',
                borderRadius: 6,
                color: C.sendText,
                cursor: 'pointer',
                fontSize: 12,
                fontWeight: 600,
                padding: '5px 14px',
                fontFamily: 'inherit',
              }}
              onClick={submit}
            >
              Send
            </button>
          </div>
        </div>
      ) : (
        <div
          style={{
            padding: '8px 6px 8px 12px',
            marginRight: 6,
            color: C.textDim,
            whiteSpace: 'pre-wrap',
            borderBottom: `1px solid ${C.nodeBorder}`,
            maxHeight: 96,
            overflowY: 'auto',
          }}
          className="nowheel ctc-scroll"
        >
          {data.prompt}
        </div>
      )}

      {/* Response blocks. nodrag: without it ReactFlow starts a node drag on
          mousedown and text selection is impossible — drag the node by its
          header/edges instead. */}
      {(blocks.length > 0 || streaming || data.error) && (
        <div
          ref={responseRef}
          className="nodrag nowheel ctc-scroll"
          onMouseUp={onResponseMouseUp}
          style={{
            padding: '4px 6px 8px 12px',
            // Inset so the scrollbar clears the right resize strip and the
            // corner thumb.
            margin: '0 6px 22px 0',
            ...(fixedHeight
              ? { flex: 1, minHeight: 0 }
              : {
                  maxHeight: 420,
                  minHeight: streaming && blocks.length === 0 ? settledHeightRef.current : undefined,
                }),
            overflowY: 'auto',
            overflowX: 'hidden',
            lineHeight: 1.5,
            userSelect: 'text',
            cursor: 'text',
          }}
        >
          {displayBlocks.map((block, i) => (
            <ResponseBlock key={i} block={block} nodeId={id} />
          ))}
          {streaming && blocks.length === 0 && (
            <p style={{ color: C.textDim }}>…</p>
          )}
          {data.error && (
            <div
              style={{
                background: C.errorBg,
                border: `1px solid ${C.error}55`,
                borderRadius: 6,
                color: C.error,
                padding: '6px 8px',
                marginTop: 6,
                whiteSpace: 'pre-wrap',
              }}
            >
              {data.error}
            </div>
          )}
        </div>
      )}

      {/* Branch-from-selection chip: floats above the resize corner while a
          response selection is active. onMouseDown preventDefault keeps the
          browser from collapsing the selection before onClick fires. */}
      {selText && data.status === 'complete' && (
        <button
          className="nodrag"
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => {
            api.branchText?.(id, selText);
            setSelText('');
            window.getSelection()?.removeAllRanges();
          }}
          title='Create a child node whose prompt quotes the selected text'
          style={{
            position: 'absolute',
            bottom: 24,
            left: 10,
            zIndex: 20,
            background: C.accent,
            border: 'none',
            borderRadius: 6,
            color: C.sendText,
            cursor: 'pointer',
            fontSize: 11,
            fontWeight: 600,
            padding: '4px 10px',
            fontFamily: 'inherit',
            boxShadow: C.shadow,
          }}
        >
          ✚ Branch from selection
        </button>
      )}

      <Handle type="source" position={Position.Bottom} style={handleStyle(C)} />
    </div>
  );
}

const nodeTypes = { chat: ChatNode };

// ── Canvas ───────────────────────────────────────────────────

function CanvasInner(props) {
  const C = useContext(ThemeCtx);
  const [nodes, setNodes] = useState([]);
  const draggingId = useRef(null);
  const resizingId = useRef(null);
  const edgeUpdateSuccessful = useRef(true);
  const { screenToFlowPosition } = useReactFlow();

  // Keyboard shortcuts on the selected node (LMCanvas parity):
  //   Ctrl/⌘+B → branch, Delete/Backspace → delete (via the confirm dialog).
  // Ignored while typing in inputs/textareas.
  const nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      if (
        t &&
        (t.tagName === 'INPUT' ||
          t.tagName === 'TEXTAREA' ||
          t.isContentEditable)
      ) {
        return;
      }
      const selected = nodesRef.current.find((n) => n.selected);
      if (!selected) return;
      if ((e.metaKey || e.ctrlKey) && (e.key === 'b' || e.key === 'B')) {
        e.preventDefault();
        props.onBranchNode?.(selected.id);
      } else if (e.key === 'Delete' || e.key === 'Backspace') {
        e.preventDefault();
        props.onDeleteNode?.(selected.id);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [props.onBranchNode, props.onDeleteNode]);

  // Server state is the source of truth; merge it in, but never yank the
  // node the user is currently dragging or resizing out from under the
  // cursor. Explicit width/height (from the resize control) becomes the
  // ReactFlow node style; width defaults to NODE_WIDTH.
  useEffect(() => {
    const incoming = props.nodes || [];
    setNodes((current) => {
      const byId = Object.fromEntries(current.map((n) => [n.id, n]));
      return incoming.map((n) => {
        const local = byId[n.id];
        if (n.id === resizingId.current && local) {
          return { ...n, position: local.position, style: local.style };
        }
        // Server geometry wins when it exists; otherwise keep whatever size
        // the user dragged locally (a lost/late resize event must not snap
        // the node back to defaults on the next unrelated state push).
        const localStyle = local?.style || {};
        const width = n.data?.width || localStyle.width || NODE_WIDTH;
        const height = n.data?.height || localStyle.height;
        const style = { width, ...(height ? { height } : {}) };
        if (n.id === draggingId.current && local) {
          return { ...n, style, position: local.position };
        }
        return { ...n, style };
      });
    });
  }, [JSON.stringify(props.nodes)]);

  const resizeApi = useMemo(
    () => ({
      start: (id) => {
        resizingId.current = id;
      },
      end: (id, params) => {
        resizingId.current = null;
        if (params) {
          props.onNodeResized?.(id, params.x, params.y, params.width, params.height);
        }
      },
    }),
    [props.onNodeResized],
  );

  // Consumed by ChatNode to derive whether a `question` block already has
  // an answering child — see ChatNode's displayBlocks and docs/RENDERERS.md.
  const childrenByParent = useMemo(() => {
    const map = new Map();
    for (const e of props.edges || []) {
      if (!map.has(e.source)) map.set(e.source, []);
      map.get(e.source).push(nodes.find((n) => n.id === e.target));
    }
    return map;
  }, [props.edges, nodes]);

  const onNodesChange = useCallback(
    (changes) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );

  const onNodeDragStart = useCallback((_e, node) => {
    draggingId.current = node.id;
  }, []);

  const onNodeDragStop = useCallback(
    (_e, node) => {
      draggingId.current = null;
      props.onNodeMoved?.(node.id, node.position.x, node.position.y);
    },
    [props.onNodeMoved],
  );

  // Manual edge creation: report to Python, which validates (single parent,
  // no cycles) and either adds the edge or toasts why not.
  const onConnect = useCallback(
    (connection) => {
      if (connection.source && connection.target) {
        props.onConnectEdge?.(connection.source, connection.target);
      }
    },
    [props.onConnectEdge],
  );

  // Edge update = drag an existing edge end onto another handle. Dropping it
  // on empty canvas instead detaches it (deletes the edge).
  const onEdgeUpdateStart = useCallback(() => {
    edgeUpdateSuccessful.current = false;
  }, []);

  const onEdgeUpdate = useCallback(
    (oldEdge, newConnection) => {
      edgeUpdateSuccessful.current = true;
      props.onEdgeReconnect?.(
        oldEdge.id,
        newConnection.source,
        newConnection.target,
      );
    },
    [props.onEdgeReconnect],
  );

  const onEdgeUpdateEnd = useCallback(
    (_e, edge) => {
      if (!edgeUpdateSuccessful.current) {
        props.onEdgeDelete?.(edge.id);
      }
      edgeUpdateSuccessful.current = true;
    },
    [props.onEdgeDelete],
  );

  // Double-click on empty pane → new root node at that spot.
  const onDoubleClick = useCallback(
    (e) => {
      if (!e.target.classList?.contains('react-flow__pane')) return;
      const pos = screenToFlowPosition({ x: e.clientX, y: e.clientY });
      props.onAddRoot?.(pos.x - NODE_WIDTH / 2, pos.y);
    },
    [screenToFlowPosition, props.onAddRoot],
  );

  return (
    <TreeApi.Provider value={childrenByParent}>
    <ResizeApi.Provider value={resizeApi}>
    <div
      style={{ width: '100%', height: '100%', background: C.bg }}
      onDoubleClick={onDoubleClick}
    >
      <ReactFlow
        nodes={nodes}
        edges={props.edges || []}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeDragStart={onNodeDragStart}
        onNodeDragStop={onNodeDragStop}
        onConnect={onConnect}
        edgesUpdatable
        onEdgeUpdateStart={onEdgeUpdateStart}
        onEdgeUpdate={onEdgeUpdate}
        onEdgeUpdateEnd={onEdgeUpdateEnd}
        connectionRadius={30}
        fitView
        fitViewOptions={{ maxZoom: 1 }}
        minZoom={0.1}
        zoomOnDoubleClick={false}
        deleteKeyCode={null}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant="dots" gap={28} size={1.5} color={C.dots} />
        <Controls position="bottom-left" showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          position="bottom-right"
          style={{ background: C.minimapBg }}
          maskColor={C.minimapMask}
          nodeColor={C.minimapNode}
        />
      </ReactFlow>
    </div>
    </ResizeApi.Provider>
    </TreeApi.Provider>
  );
}

// Palette-dependent global styles for the canvas subtree.
function themeCss(C) {
  return `
    @keyframes ctc-pulse {
      0%, 100% { box-shadow: 0 0 0 0 ${C.pulse}; }
      50% { box-shadow: 0 0 0 5px ${C.pulseFaint}; }
    }
    .ctc-md p { margin: 6px 0; }
    .ctc-md h1, .ctc-md h2, .ctc-md h3 { font-size: 14px; margin: 10px 0 4px 0; }
    .ctc-md ul, .ctc-md ol { margin: 4px 0; padding-left: 20px; }
    .ctc-md pre {
      background: ${C.codeBg}; border: 1px solid ${C.codeBorder}; border-radius: 6px;
      padding: 8px; overflow-x: auto; font-size: 11.5px; line-height: 1.4;
    }
    .ctc-md code { background: ${C.codeBg}; border-radius: 4px; padding: 1px 4px; font-size: 11.5px; }
    .ctc-md pre code { background: transparent; padding: 0; }
    .ctc-md table, .ctc-table { border-collapse: collapse; font-size: 12px; margin: 6px 0; }
    .ctc-md th, .ctc-md td, .ctc-table th, .ctc-table td {
      border: 1px solid ${C.nodeBorder}; padding: 3px 8px; text-align: left;
    }
    .ctc-table th { background: ${C.codeBg}; position: sticky; top: 0; }
    .ctc-md a { color: ${C.accent}; }
    .ctc-md blockquote {
      border-left: 3px solid ${C.nodeBorder}; margin: 6px 0; padding-left: 10px; color: ${C.textDim};
    }
    /* Confirmed live (2026-07-06): resolving var(--ctc-highlight) to a literal
       rgb() here (see the getComputedStyle probe in ChatCanvas() below) was
       NOT the fix — the shadow root's <style> tag verifiably contained the
       correct literal color, yet Chromium still painted the native default
       thumb. Chromium's ::-webkit-scrollbar-* pseudo-element theming has a
       known gap: it doesn't apply to scrollable elements hosted inside a
       shadow root at all, independent of whether the declared value is
       correct. scrollbar-color/scrollbar-width are regular standard CSS
       properties (not the legacy UA-pseudo mechanism) and are unaffected —
       kept as the primary rule. The -webkit- pseudo rules stay as a fallback
       for browsers without scrollbar-color support (older Safari) rendering
       in the light DOM (app_rx), where they've always worked fine.
    */
    .ctc-scroll {
      scrollbar-width: thin;
      scrollbar-color: ${C.scrollThumb} transparent;
    }
    .ctc-scroll::-webkit-scrollbar { width: 8px; height: 8px; }
    .ctc-scroll::-webkit-scrollbar-track { background: transparent; }
    .ctc-scroll::-webkit-scrollbar-thumb {
      background: ${C.scrollThumb}; border-radius: 4px;
    }
    .ctc-scroll::-webkit-scrollbar-thumb:hover { background: ${C.scrollThumbHover}; }
    .react-flow__resize-control.line {
      transition: border-color 0.15s;
    }
    .react-flow__resize-control.line:hover {
      border-color: color-mix(in srgb, ${C.icon} 60%, transparent) !important;
    }
    .react-flow__controls { box-shadow: none; }
    .react-flow__controls button {
      background: ${C.ctrlBg}; border-bottom: 1px solid ${C.ctrlBorder}; fill: ${C.textDim};
    }
    .react-flow__controls button:hover { background: ${C.ctrlHover}; }
    .react-flow__edge-path { stroke: ${C.edge}; stroke-width: 1.5; }
    .react-flow__handle { cursor: crosshair; }
    .react-flow__handle:hover { background: ${C.icon} !important; }
    .react-flow__connection-path { stroke: ${C.accent}; }
  `;
}

export function ChatCanvas(props) {
  const mode = props.mode === 'light' ? 'light' : 'dark';
  const C = PALETTES[mode];

  const api = useMemo(
    () => ({
      submit: props.onSubmitPrompt,
      branch: props.onBranchNode,
      branchText: props.onBranchFromText,
      duplicate: props.onDuplicateNode,
      clear: props.onClearNode,
      del: props.onDeleteNode,
      answerQuestion: props.onAnswerQuestion,
      cascading: Boolean(props.cascadingReruns),
    }),
    [
      props.onSubmitPrompt,
      props.onBranchNode,
      props.onBranchFromText,
      props.onDuplicateNode,
      props.onClearNode,
      props.onDeleteNode,
      props.onAnswerQuestion,
      props.cascadingReruns,
    ],
  );

  // Bakes var(--ctc-highlight) etc. into a literal computed color via
  // getComputedStyle before it reaches the stylesheet below. NOT what fixed
  // the shadow-DOM scrollbar-thumb bug (confirmed live: the shadow root's
  // <style> tag already contained the correct literal color and the browser
  // still wouldn't paint it — see the scrollbar-color rule in themeCss() for
  // the actual fix). Kept anyway as a cheap defensive measure for any other
  // var()-based value that might hit a similar shadow-DOM resolution gap;
  // harmless no-op in light-DOM hosts (app_rx).
  const scrollAnchorRef = useRef(null);
  const [resolvedScroll, setResolvedScroll] = useState(null);
  useEffect(() => {
    const anchor = scrollAnchorRef.current;
    if (!anchor) return;
    const resolve = (value) => {
      if (typeof value !== 'string' || !value.includes('var(')) return value;
      const probe = document.createElement('span');
      probe.style.color = value;
      anchor.appendChild(probe);
      const resolved = getComputedStyle(probe).color;
      anchor.removeChild(probe);
      return resolved;
    };
    setResolvedScroll({
      scrollThumb: resolve(C.scrollThumb),
      scrollThumbHover: resolve(C.scrollThumbHover),
    });
  }, [C.scrollThumb, C.scrollThumbHover]);

  const cssVars = resolvedScroll ? { ...C, ...resolvedScroll } : C;

  return (
    <CanvasApi.Provider value={api}>
      <ThemeCtx.Provider value={C}>
        <div ref={scrollAnchorRef} style={{ display: 'contents' }}>
          <style>{themeCss(cssVars)}</style>
        </div>
        <ReactFlowProvider>
          <CanvasInner {...props} />
        </ReactFlowProvider>
      </ThemeCtx.Provider>
    </CanvasApi.Provider>
  );
}

export default ChatCanvas;
