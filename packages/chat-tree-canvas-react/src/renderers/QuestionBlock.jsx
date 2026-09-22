// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "question", "text": str} — pauses the turn for a one-line answer.
// Submitting creates a new child node whose prompt is the composed Q&A
// (chat_tree.blocks.compose_followup_prompt) and auto-submits it. This
// component only ever renders the UNANSWERED state — once a node has a
// child whose prompt matches this question, ChatNode (ChatCanvas.jsx)
// substitutes a plain text block instead of this one, so there's no
// separate "resolved" UI to build or keep in sync here.
import React, { useCallback, useContext, useState } from 'react';
import { ThemeCtx } from './theme.jsx';
import { CanvasApi } from './canvasApi.jsx';

export function QuestionBlock({ block, nodeId }) {
  const C = useContext(ThemeCtx);
  const api = useContext(CanvasApi);
  const [answer, setAnswer] = useState('');

  const submit = useCallback(() => {
    const text = answer.trim();
    if (text) api.answerQuestion?.(nodeId, block.text, text);
  }, [api, nodeId, block.text, answer]);

  const onKeyDown = useCallback(
    (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
        e.preventDefault();
        submit();
      }
    },
    [submit],
  );

  return (
    <div style={{ margin: '6px 0' }}>
      <div style={{ fontSize: 13, fontWeight: 600, color: C.text, marginBottom: 6 }}>
        {block.text}
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          className="nodrag"
          autoFocus
          value={answer}
          placeholder="Your answer…  (Ctrl+Enter to send)"
          onChange={(e) => setAnswer(e.target.value)}
          onKeyDown={onKeyDown}
          style={{
            flex: 1,
            minWidth: 0,
            boxSizing: 'border-box',
            background: C.inputBg,
            border: `1px solid ${C.nodeBorder}`,
            borderRadius: 8,
            color: C.text,
            fontFamily: 'inherit',
            fontSize: 13,
            padding: '6px 8px',
            outline: 'none',
          }}
        />
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
  );
}

export default QuestionBlock;
