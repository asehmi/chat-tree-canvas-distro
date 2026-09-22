// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// chat-tree-canvas-react — public API.
//
// The package ships raw JSX (no build step): Vite/esbuild hosts consume it
// directly. Import the CSS variables it themes against once per app:
//   import 'chat-tree-canvas-react/theme.css';
// (or define --highlight etc. yourself — see README.md).
export { ChatCanvas, default } from './ChatCanvas.jsx';
export { ResponseBlock, RENDERERS } from './renderers/index.jsx';
export { ThemeCtx, PALETTES } from './renderers/theme.jsx';
export { useWidthScaledHeight } from './renderers/useWidthScaledHeight.js';
