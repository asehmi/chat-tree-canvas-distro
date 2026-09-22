// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "chart", "title": str, "figure": str} — Plotly chart from figure
// JSON. Height follows the node width at a ~5:3 aspect so widening the node
// un-squashes complex charts instead of only stretching them horizontally.
import React, { useContext, useEffect, useMemo, useRef } from 'react';
import PlotlyModule from 'plotly.js-dist-min';
import { ThemeCtx } from './theme.jsx';
import { useWidthScaledHeight } from './useWidthScaledHeight.js';

// plotly.js-dist-min is CJS; depending on Vite's interop the default import
// is either the module itself or a namespace object wrapping it.
const Plotly = PlotlyModule?.react ? PlotlyModule : PlotlyModule?.default;

function Plot({ data, layout, style }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current && Plotly?.react) {
      // No `responsive: true` — height/width are already fully driven by
      // useWidthScaledHeight below. Letting Plotly *also* attach its own
      // ResizeObserver to the same div raced its async auto-margin redraw
      // against these React-driven Plotly.react calls (both touch
      // gd._fullLayout), which is what threw
      // "Cannot read properties of undefined (reading
      // '_redrawFromAutoMarginCount')" during a node resize drag.
      Plotly.react(ref.current, data, layout, { displayModeBar: false });
    }
  }, [data, layout]);
  useEffect(() => {
    const el = ref.current;
    return () => {
      if (el && Plotly?.purge) Plotly.purge(el);
    };
  }, []);
  return <div ref={ref} style={style} />;
}

export function ChartBlock({ block }) {
  const C = useContext(ThemeCtx);
  const wrapRef = useRef(null);
  const chartHeight = useWidthScaledHeight(wrapRef, 0.62, 240, 620);

  const fig = useMemo(() => {
    try {
      return JSON.parse(block.figure);
    } catch {
      return null;
    }
  }, [block.figure]);
  // Memoized so unrelated re-renders (e.g. a sibling text block streaming in
  // the same node) don't recreate this object's identity — Plot's effect
  // keys off [data, layout], so a new identity re-fires Plotly.react. Must
  // run before the `!fig` early return below (Rules of Hooks: same hooks,
  // every render), so it tolerates fig being null.
  const layout = useMemo(
    () => ({
      ...fig?.layout,
      autosize: true,
      height: chartHeight,
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: 'rgba(0,0,0,0)',
      font: { ...(fig?.layout?.font || {}), color: C.text, size: 10 },
      margin: { l: 40, r: 16, t: 32, b: 32 },
    }),
    [fig, chartHeight, C.text],
  );
  if (!fig) {
    return (
      <div style={{ fontSize: 11, color: C.error }}>
        [chart: {block.title || 'untitled'} — invalid figure JSON]
      </div>
    );
  }
  return (
    <div
      ref={wrapRef}
      className="nodrag nowheel"
      style={{
        margin: '6px 0',
        border: `1px solid ${C.nodeBorder}`,
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      <Plot
        data={fig.data || []}
        layout={layout}
        style={{ width: '100%', height: chartHeight }}
      />
    </div>
  );
}

export default ChartBlock;
