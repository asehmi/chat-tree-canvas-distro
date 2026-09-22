// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "table", "title": str, "records": [dict]} — scrollable table
// (first 30 rows shown; columns from the first record's keys).
import React, { useContext, useRef } from 'react';
import { ThemeCtx } from './theme.jsx';
import { useWidthScaledHeight } from './useWidthScaledHeight.js';

export function TableBlock({ block }) {
  const C = useContext(ThemeCtx);
  const wrapRef = useRef(null);
  const maxHeight = useWidthScaledHeight(wrapRef, 0.5, 220, 460);
  const records = block.records || [];
  if (!records.length) return null;
  const cols = Object.keys(records[0]);
  const shown = records.slice(0, 30);
  return (
    <div ref={wrapRef} style={{ margin: '6px 0' }}>
      {block.title && (
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 4 }}>
          {block.title}
        </div>
      )}
      <div className="nowheel" style={{ maxHeight, overflow: 'auto' }}>
        <table className="ctc-table">
          <thead>
            <tr>
              {cols.map((c) => (
                <th key={c}>{c}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map((rec, i) => (
              <tr key={i}>
                {cols.map((c) => (
                  <td key={c}>{String(rec[c] ?? '')}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {records.length > shown.length && (
        <div style={{ fontSize: 10, color: C.textDim }}>
          …{records.length - shown.length} more rows
        </div>
      )}
    </div>
  );
}

export default TableBlock;
