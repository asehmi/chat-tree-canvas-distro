// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "metric", "label": str, "value": any, "unit": str} — stat tile.
import React, { useContext } from 'react';
import { ThemeCtx } from './theme.jsx';

export function MetricBlock({ block }) {
  const C = useContext(ThemeCtx);
  return (
    <div
      style={{
        display: 'inline-block',
        background: C.codeBg,
        border: `1px solid ${C.nodeBorder}`,
        borderRadius: 8,
        padding: '6px 12px',
        margin: '6px 6px 6px 0',
      }}
    >
      <div style={{ fontSize: 10, color: C.textDim }}>{block.label}</div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>
        {block.value}
        {block.unit ? (
          <span style={{ fontSize: 11, color: C.textDim, marginLeft: 4 }}>
            {block.unit}
          </span>
        ) : null}
      </div>
    </div>
  );
}

export default MetricBlock;
