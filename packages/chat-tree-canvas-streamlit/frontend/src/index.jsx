// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// Streamlit components-v2 FrontendRenderer wrapping chat-tree-canvas-react.
//
// The component renders in the page DOM inside a shadow root
// (isolate_styles=True). Two consequences handled here:
//   1. ReactFlow's stylesheet is bundled via the CSS import below (Streamlit
//      injects the built style-*.css into the shadow root).
//   2. `:root { --vars }` don't match inside a shadow root, so the canvas's
//      namespaced theme variables (var(--ctc-highlight), …) are set inline
//      on the wrapper element, per mode. The `--ctc-` prefix (not the more
//      generic `--highlight` etc. app_rx's own themes.css uses) exists so
//      the package's public variable contract can't collide with a host's
//      own pre-existing tokens of the same generic shape.
//
// Every canvas callback becomes one transient trigger value:
//   setTriggerValue("event", { type, payload: [...], nonce })
// The Python wrapper returns it from chat_tree_canvas() for exactly one rerun.
//
// Streaming: a "full sync" call (data.nodes/data.edges present) is the
// source of truth and seeds `treeCache`. A "patch" call (data.patch =
// {node_id, data} present instead) merges into the CACHED node array —
// only the touched node gets a new object; every other node keeps the same
// object reference it had before (ChatNode itself isn't memoized — see
// ChatCanvas.jsx history — but this still bounds what changes per push).
// This is what lets a Python-side loop push one token at a time (see
// app_st/controller.py) without re-serializing the whole tree per token.
//
// Full syncs ALSO go through mergeById (below) rather than handing ReactFlow
// a wholesale-fresh object per node: args are JSON round-tripped from Python
// every call regardless of patch/full, so even an in-place Python mutation
// arrives here as a brand-new JS object for every node. Without the merge,
// EVERY full sync (session load, or the one that follows right after a
// stream/cascade finishes) would hand ReactFlow all-new node references,
// which was traced to two visible bugs: edges transiently anchoring at a
// node's raw top-left corner instead of its measured center (ReactFlow
// falling back to width=0 for a node it treats as newly seen), and fitView
// re-triggering (an unwanted re-fit/flash) at the same moments. Reusing the
// previous reference for any node/edge whose content is unchanged keeps
// those id's identity stable across a full sync, same as the patch path
// already did for the one node being streamed into.
import React from 'react';
import { createRoot } from 'react-dom/client';
import ChatCanvas from 'chat-tree-canvas-react';
import 'reactflow/dist/style.css';

// Performance-theme variables, namespaced with the canvas package's --ctc-
// prefix (mirrors chat-tree-canvas-react/theme.css, which can't be used
// as-is because its :root selector is inert in shadow DOM).
const THEME_VARS = {
  light: {
    '--ctc-primary': '#004CA3',
    '--ctc-primary-accent': '#0a85ff',
    '--ctc-highlight': '#64A923',
    '--ctc-secondary': '#99ccff',
    '--ctc-default': '#2a3e50',
    '--ctc-dark': '#282c34',
    '--ctc-dim': '#808080',
    '--ctc-light': '#F5F5F5',
  },
  dark: {
    '--ctc-primary': '#5277AD',
    '--ctc-primary-accent': '#013E7F',
    '--ctc-highlight': '#64A923',
    '--ctc-secondary': '#001025',
    '--ctc-default': '#828F9B',
    '--ctc-dark': '#fffdfb',
    '--ctc-dim': '#474747',
    '--ctc-light': '#000000',
  },
};

const CALLBACKS = [
  ['onSubmitPrompt', 'submit_prompt'],
  ['onBranchNode', 'branch_node'],
  ['onBranchFromText', 'branch_from_text'],
  ['onDuplicateNode', 'duplicate_node'],
  ['onClearNode', 'clear_node'],
  ['onDeleteNode', 'delete_node'],
  ['onAddRoot', 'add_root'],
  ['onNodeMoved', 'node_moved'],
  ['onNodeResized', 'node_resized'],
  ['onConnectEdge', 'connect_edge'],
  ['onEdgeReconnect', 'edge_reconnect'],
  ['onEdgeDelete', 'edge_delete'],
  ['onAnswerQuestion', 'answer_question'],
];

const reactRoots = new WeakMap();
// Last full-sync {nodes, edges} per component instance, patched in place by
// streaming pushes. See the streaming note in the header comment.
const treeCache = new WeakMap();

// Reuse `prev`'s object for any item whose JSON content is unchanged, so
// ReactFlow sees the same reference across a full sync for anything that
// didn't actually change. Comparing via JSON.stringify is conservative, not
// exact (differing key insertion order would compare unequal even for
// logically-identical content) — that only costs a missed reuse, never a
// wrong one, since two DIFFERENT contents can never stringify the same.
function mergeById(prevList, nextList) {
  const prevById = new Map(prevList.map((item) => [item.id, item]));
  return nextList.map((item) => {
    const prev = prevById.get(item.id);
    if (prev && (prev === item || JSON.stringify(prev) === JSON.stringify(item))) {
      return prev;
    }
    return item;
  });
}

const ChatTreeCanvasRenderer = (args) => {
  const { data, parentElement, setTriggerValue } = args;

  let root = reactRoots.get(parentElement);
  if (!root) {
    const host = parentElement.querySelector('.ctc-st-root') || parentElement;
    root = createRoot(host);
    reactRoots.set(parentElement, root);
  }

  const prevTree = treeCache.get(parentElement);
  let tree;
  if (data.patch) {
    if (!prevTree) return; // patch arrived before any full sync — nothing to merge into
    const { node_id, data: patchData } = data.patch;
    tree = {
      nodes: prevTree.nodes.map((n) => (n.id === node_id ? { ...n, data: patchData } : n)),
      edges: prevTree.edges,
    };
  } else {
    tree = {
      nodes: mergeById(prevTree?.nodes || [], data.nodes || []),
      edges: mergeById(prevTree?.edges || [], data.edges || []),
    };
  }
  treeCache.set(parentElement, tree);

  const mode = data.mode === 'light' ? 'light' : 'dark';
  const handlers = {};
  for (const [prop, type] of CALLBACKS) {
    handlers[prop] = (...payload) =>
      setTriggerValue('event', {
        type,
        payload,
        nonce:
          (globalThis.crypto && globalThis.crypto.randomUUID)
            ? globalThis.crypto.randomUUID()
            : String(Date.now()) + Math.random(),
      });
  }

  root.render(
    <div
      style={{
        width: '100%',
        height: (data.height || 760) + 'px',
        ...THEME_VARS[mode],
      }}
    >
      <ChatCanvas
        nodes={tree.nodes}
        edges={tree.edges}
        mode={mode}
        cascadingReruns={Boolean(data.cascadingReruns)}
        {...handlers}
      />
    </div>,
  );
};

export default ChatTreeCanvasRenderer;
