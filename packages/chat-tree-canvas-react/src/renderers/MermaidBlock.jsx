// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "mermaid", "title": str, "code": str} — structured diagram from a
// custom_script RESULT line. Thin adapter; MermaidDiagram (shared with
// TextBlock's inline ```mermaid fence path) does the actual rendering.
import React from 'react';
import { MermaidDiagram } from './MermaidDiagram.jsx';

export function MermaidBlock({ block }) {
  return <MermaidDiagram code={block.code} title={block.title} />;
}

export default MermaidBlock;
