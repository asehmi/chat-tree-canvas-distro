// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// Renderers namespace: one file per typed response block, dispatched by
// block.kind. The block shapes mirror the provider event contract
// (docs/PROVIDERS.md); the step-by-step guide for adding a custom kind —
// provider event → Python reducer → renderer → this registry — is
// docs/RENDERERS.md.
import React from 'react';
import TextBlock from './TextBlock.jsx';
import ToolBlock from './ToolBlock.jsx';
import ScriptBlock from './ScriptBlock.jsx';
import MetricBlock from './MetricBlock.jsx';
import TableBlock from './TableBlock.jsx';
import ChartBlock from './ChartBlock.jsx';
import MermaidBlock from './MermaidBlock.jsx';
import QuestionBlock from './QuestionBlock.jsx';

export const RENDERERS = {
  text: TextBlock,
  tool: ToolBlock,
  script: ScriptBlock,
  metric: MetricBlock,
  table: TableBlock,
  chart: ChartBlock,
  mermaid: MermaidBlock,
  question: QuestionBlock,
};

// `nodeId` is an opt-in second prop, ignored by every renderer except
// QuestionBlock — see "Renderer conventions" in docs/RENDERERS.md for why.
export function ResponseBlock({ block, nodeId }) {
  const Renderer = RENDERERS[block.kind];
  return Renderer ? <Renderer block={block} nodeId={nodeId} /> : null;
}

export { ThemeCtx, PALETTES } from './theme.jsx';
export { CanvasApi } from './canvasApi.jsx';
export { useWidthScaledHeight } from './useWidthScaledHeight.js';

export default ResponseBlock;
