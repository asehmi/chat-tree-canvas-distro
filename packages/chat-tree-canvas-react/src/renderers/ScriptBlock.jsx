// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "script", "code": str} — collapsible generated-script panel.
import React, { useContext, useRef } from 'react';
import { ThemeCtx } from './theme.jsx';
import { useWidthScaledHeight } from './useWidthScaledHeight.js';

export function ScriptBlock({ block }) {
  const C = useContext(ThemeCtx);
  const wrapRef = useRef(null);
  const maxHeight = useWidthScaledHeight(wrapRef, 0.5, 220, 460);
  return (
    <details ref={wrapRef} style={{ margin: '6px 0' }}>
      <summary
        style={{ cursor: 'pointer', fontSize: 11, color: C.textDim }}
        className="nodrag"
      >
        🐍 generated script ({block.code.split('\n').length} lines)
      </summary>
      <pre
        style={{
          background: C.codeBg,
          border: `1px solid ${C.codeBorder}`,
          borderRadius: 6,
          padding: 8,
          overflowX: 'auto',
          fontSize: 11,
          lineHeight: 1.4,
          maxHeight,
          overflowY: 'auto',
        }}
        className="nowheel"
      >
        {block.code}
      </pre>
    </details>
  );
}

export default ScriptBlock;
