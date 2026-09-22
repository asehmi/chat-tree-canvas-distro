// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// Shared canvas theme: palettes (Performance theme, light/dark) and the
// React context that carries the active palette to every node and block
// renderer. Context (rather than props) on purpose — context updates pierce
// ReactFlow's node memoization, so a mode toggle re-renders everything.
//
// Header action icons and several chrome colors reference the canvas
// package's own namespaced CSS variables (var(--ctc-highlight) etc., defined
// in theme.css or aliased by a host's own themes.css) so they track the
// active body theme classes live without colliding with a host's more
// generic --highlight/--dark/etc. tokens of the same shape.
import { createContext } from 'react';

export const PALETTES = {
  dark: {
    // Surfaced on the context value itself (not derived by consumers) so a
    // renderer that needs light/dark as a plain flag — e.g. MermaidBlock,
    // which can't express "themed via CSS vars" because mermaid.render()
    // bakes colors into the SVG at render time — doesn't need its own prop
    // or a second context. Static per palette, not computed: PALETTES.dark
    // is always the dark palette, so there's nothing to derive at runtime.
    mode: 'dark',
    isDark: true,
    // Canvas pane background (user request) — was hardcoded to #101014,
    // never actually wired to the theme var despite the name suggesting it
    // tracked it. var(--ctc-dark) inverts per mode like --ctc-light does
    // elsewhere in this theme (see theme.css), so this intentionally shows
    // near-white in dark mode / dark slate in light mode, matching the
    // app's own bg-dark-driven outer background.
    bg: 'var(--ctc-dark)',
    node: '#17171c',
    nodeBorder: '#2c2c34',
    // Outer node frame only (theme highlight, per user request) — internal
    // separators and small control borders stay on nodeBorder.
    // var(--ctc-highlight) resolves from theme.css (or a host's alias of it)
    // so it tracks the active body theme classes.
    nodeBorderOuter: 'var(--ctc-highlight)',
    nodeBorderSelected: '#7c8cf8',
    accent: '#7c8cf8',
    icon: 'var(--ctc-highlight)',
    text: '#e8e8ea',
    textDim: '#9a9aa4',
    error: '#f87c7c',
    errorBg: 'rgba(248,124,124,0.08)',
    codeBg: '#0d0d12',
    codeBorder: '#26262e',
    inputBg: '#101015',
    handle: '#4a4a58',
    dots: '#2a2a33',
    edge: '#4a4a58',
    ctrlBg: '#17171c',
    ctrlBorder: '#2c2c34',
    ctrlHover: '#22222a',
    minimapBg: '#17171c',
    minimapMask: 'rgba(16,16,20,0.7)',
    minimapNode: '#3a3a46',
    // Theme-highlight scrollbars in dark mode (user request).
    scrollThumb: 'var(--ctc-highlight)',
    scrollThumbHover: 'color-mix(in srgb, var(--ctc-highlight) 75%, white)',
    sendText: '#0e0e14',
    shadow: '0 4px 16px rgba(0,0,0,0.35)',
    shadowSelected: '0 8px 24px rgba(0,0,0,0.45)',
    pulse: 'rgba(124,140,248,0.35)',
    pulseFaint: 'rgba(124,140,248,0.08)',
    resizeBg: 'rgba(23,23,28,0.9)',
  },
  light: {
    // See the dark palette's mode/isDark comment — same reasoning.
    mode: 'light',
    isDark: false,
    // See the dark palette's bg comment — same fix, same var.
    bg: 'var(--ctc-dark)',
    node: '#ffffff',
    nodeBorder: '#d9dee8',
    // Outer node frame only, same as dark — internal separators and small
    // control borders stay on nodeBorder. Was hardcoded to #d9dee8 (a plain
    // gray) until now — a leftover from before highlight-theming was added,
    // only ever applied to the dark palette below.
    nodeBorderOuter: 'var(--ctc-highlight)',
    nodeBorderSelected: '#6366f1',
    accent: '#004CA3',
    icon: 'var(--ctc-highlight)',
    text: '#2a3e50',
    textDim: '#64748b',
    error: '#dc2626',
    errorBg: 'rgba(220,38,38,0.07)',
    codeBg: '#eef1f6',
    codeBorder: '#dde2ec',
    inputBg: '#fafbfd',
    handle: '#94a3b8',
    dots: '#c9cfd9',
    edge: '#94a3b8',
    ctrlBg: '#ffffff',
    ctrlBorder: '#d9dee8',
    ctrlHover: '#eef1f6',
    minimapBg: '#ffffff',
    minimapMask: 'rgba(245,245,245,0.75)',
    minimapNode: '#c6cdd9',
    // Same reasoning as nodeBorderOuter above — was hardcoded gray, now
    // matches the dark palette's theme-highlight scrollbar.
    scrollThumb: 'var(--ctc-highlight)',
    scrollThumbHover: 'color-mix(in srgb, var(--ctc-highlight) 75%, white)',
    sendText: '#ffffff',
    shadow: '0 4px 14px rgba(15,23,42,0.10)',
    shadowSelected: '0 8px 24px rgba(15,23,42,0.18)',
    pulse: 'rgba(99,102,241,0.35)',
    pulseFaint: 'rgba(99,102,241,0.08)',
    resizeBg: 'rgba(255,255,255,0.92)',
  },
};

export const ThemeCtx = createContext(PALETTES.dark);
