// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "tool", "tool": str, "status": "called"|"ok"|"denied", "reason": str}
// One-line tool invocation status.
import React, { useContext } from 'react';
import { ThemeCtx } from './theme.jsx';

export function ToolBlock({ block }) {
  const C = useContext(ThemeCtx);
  const denied = block.status === 'denied';
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: 6,
        margin: '6px 0',
        fontSize: 11,
        color: denied ? C.error : C.textDim,
      }}
    >
      <span>{denied ? '⛔' : '🔧'}</span>
      <code
        style={{
          background: C.codeBg,
          borderRadius: 4,
          padding: '1px 6px',
        }}
      >
        {block.tool}
      </code>
      {block.status === 'called' && <span>running…</span>}
      {block.status === 'ok' && <span>✓</span>}
      {denied && <span>denied: {block.reason}</span>}
    </div>
  );
}

export default ToolBlock;
