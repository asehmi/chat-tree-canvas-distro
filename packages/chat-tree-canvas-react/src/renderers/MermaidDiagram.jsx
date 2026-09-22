// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// Shared mermaid presentational component. Both MermaidBlock (structured
// `kind:mermaid`) and TextBlock's inline ```mermaid fence render through
// this, so a diagram looks the same however it arrived. Theme detection and
// the source-expander both live here (one place) rather than duplicated per
// path.
import React, { useContext } from 'react';
import { ThemeCtx } from './theme.jsx';
import { useMermaidSvg } from './useMermaidSvg.js';

export function MermaidDiagram({ code, title = '' }) {
  const C = useContext(ThemeCtx);
  // ThemeCtx carries isDark directly (see theme.jsx's PALETTES) — mermaid
  // bakes colors into its SVG output at render time rather than reading CSS
  // variables, so it needs light/dark as a plain flag, not a themed color.
  const isDark = C.isDark;
  const { svg, error } = useMermaidSvg(code, isDark);

  return (
    <div style={{ margin: '6px 0' }}>
      {title ? (
        <div style={{ fontSize: 10, color: C.textDim, marginBottom: 4 }}>{title}</div>
      ) : null}
      {error ? (
        <div style={{ fontSize: 11, color: C.error, margin: '4px 0' }}>
          [mermaid: {title || 'diagram'} — {error}]
        </div>
      ) : (
        <div
          className="nowheel ctc-scroll"
          style={{
            border: `1px solid ${C.nodeBorder}`,
            borderRadius: 8,
            padding: 8,
            overflow: 'auto',
            maxHeight: 520,
            background: C.codeBg,
          }}
          // svg is mermaid output rendered with securityLevel:'strict' (scripts
          // stripped) — the source is untrusted (sandbox script output or
          // model prose), so strict matters.
          dangerouslySetInnerHTML={{ __html: svg }}
        />
      )}
      {/* Source expander, same <details>/<summary> convention as ScriptBlock.jsx.
          Auto-open on error so a broken diagram's source is immediately visible
          instead of costing an extra click right when it's most useful. */}
      <details open={Boolean(error)} style={{ marginTop: 4 }}>
        <summary
          style={{ cursor: 'pointer', fontSize: 10, color: C.textDim }}
          className="nodrag"
        >
          mermaid source
        </summary>
        <pre
          className="nowheel"
          style={{
            background: C.codeBg,
            border: `1px solid ${C.codeBorder}`,
            borderRadius: 6,
            padding: 8,
            overflowX: 'auto',
            fontSize: 11,
            lineHeight: 1.4,
            maxHeight: 300,
            overflowY: 'auto',
            marginTop: 4,
          }}
        >
          {code}
        </pre>
      </details>
    </div>
  );
}

export default MermaidDiagram;
