// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

import { useEffect, useState } from 'react';

// Scale a block's height with its rendered width (clamped) so resizing the
// node gives content vertical room too, not just horizontal stretch. The
// ResizeObserver follows the node resize control live.
export function useWidthScaledHeight(ref, ratio, min, max) {
  const [height, setHeight] = useState(min);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    // Coalesce to one update per frame — a live node-resize drag fires the
    // observer far faster than that, and downstream consumers (e.g.
    // ChartBlock's Plotly instance) redraw on every height change, so an
    // unthrottled stream of updates thrashes them.
    let raf = 0;
    const ro = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect?.width;
      if (!width) return;
      if (raf) cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => {
        setHeight(Math.max(min, Math.min(max, Math.round(width * ratio))));
      });
    });
    ro.observe(el);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      ro.disconnect();
    };
  }, [ref, ratio, min, max]);
  return height;
}

export default useWidthScaledHeight;
