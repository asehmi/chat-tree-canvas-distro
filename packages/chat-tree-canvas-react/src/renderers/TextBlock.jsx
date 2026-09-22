// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// {"kind": "text", "text": str} — markdown (GFM) rendered prose. A
// ```mermaid fenced block renders as a diagram (shared MermaidDiagram, same
// component the structured `mermaid` block uses) instead of a code box; every
// other language and inline code renders normally. Styling from the .ctc-md
// rules generated in chat_canvas.jsx.
import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { MermaidDiagram } from './MermaidDiagram.jsx';

const MD_COMPONENTS = {
  code({ className, children, ...props }) {
    if (/\blanguage-mermaid\b/.test(className || '')) {
      return <MermaidDiagram code={String(children).replace(/\n$/, '')} />;
    }
    return (
      <code className={className} {...props}>
        {children}
      </code>
    );
  },
  // react-markdown wraps a fenced block in <pre><code>. For a mermaid fence,
  // unwrap the <pre> so the diagram isn't rendered inside a code box; the
  // child <code> override above produces the diagram. Non-mermaid <pre> is
  // left untouched.
  pre({ children, ...props }) {
    const child = React.Children.toArray(children)[0];
    const cls = child?.props?.className || '';
    if (/\blanguage-mermaid\b/.test(cls)) return <>{children}</>;
    return <pre {...props}>{children}</pre>;
  },
};

export function TextBlock({ block }) {
  return (
    <div className="ctc-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
        {block.text}
      </ReactMarkdown>
    </div>
  );
}

export default TextBlock;
