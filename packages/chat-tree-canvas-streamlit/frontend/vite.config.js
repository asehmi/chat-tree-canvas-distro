// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Library-style build: one ES module (default-exporting the components-v2
// FrontendRenderer) + one CSS file, emitted into the python package's
// static/ dir where [tool.streamlit.component] asset_dir points. React and
// all canvas deps are bundled — the component runs in the host page's DOM
// (shadow root), not an iframe, and must be self-contained.
export default defineConfig({
  plugins: [react()],
  resolve: {
    // chat-tree-canvas-react is an npm file: symlink; resolve its bare
    // imports from THIS project's node_modules (single React copy) instead
    // of walking up from the package's real path, where nothing exists.
    preserveSymlinks: true,
  },
  build: {
    outDir: '../chat_tree_canvas_st/static',
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: 'src/index.jsx',
      output: {
        format: 'es',
        entryFileNames: 'index-[hash].js',
        assetFileNames: 'style-[hash][extname]',
      },
      preserveEntrySignatures: 'exports-only',
    },
  },
});
