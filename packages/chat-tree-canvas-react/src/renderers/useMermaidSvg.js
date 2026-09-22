// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// Shared mermaid render hook — used by both MermaidDiagram's structured-block
// path and TextBlock's inline ```mermaid fence path. mermaid.render is async
// and returns { svg }; theme is set per-render via an %%{init}%% directive so
// light/dark toggles re-render without a global re-initialize race. One place
// owns the mermaid.render call; callers just pass code + isDark.
import { useEffect, useId, useMemo, useState } from 'react';
import mermaid from 'mermaid';

let _initialized = false;
function ensureInit() {
  if (_initialized) return;
  // securityLevel:'strict' — mermaid strips scripts/HTML from diagram source
  // (the source is untrusted: sandbox script output or model prose).
  mermaid.initialize({ startOnLoad: false, securityLevel: 'strict' });
  _initialized = true;
}

export function useMermaidSvg(code, isDark) {
  const rawId = useId();
  // mermaid.render needs a DOM-id-safe, unique target id per call.
  const domId = useMemo(() => 'mmd-' + rawId.replace(/[^a-zA-Z0-9]/g, ''), [rawId]);
  const [svg, setSvg] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    let cancelled = false;
    ensureInit();
    const src = (code || '').trim();
    if (!src) {
      setSvg('');
      setError('empty diagram');
      return;
    }
    const themed = `%%{init: {'theme': '${isDark ? 'dark' : 'neutral'}'}}%%\n` + src;
    mermaid
      .render(domId, themed)
      .then(({ svg: out }) => {
        if (cancelled) return;
        // Make the SVG fluid inside its container, not its intrinsic px width.
        setSvg(out.replace(/max-width:\s*[\d.]+px/, 'max-width: 100%'));
        setError('');
      })
      .catch((e) => {
        if (cancelled) return;
        setError(String(e?.message || e));
        setSvg('');
      });
    return () => {
      cancelled = true;
    };
  }, [code, domId, isDark]);

  return { svg, error };
}

export default useMermaidSvg;
