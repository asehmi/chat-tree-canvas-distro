// Copyright (c) 2026 Arvindra Sehmi
// Licensed under the MIT License — see LICENSE for details.

// CanvasApi context — defined here (not in ChatCanvas.jsx) so renderer
// files can consume it without a circular import: ChatCanvas.jsx already
// imports FROM renderers/index.jsx, so a renderer importing CanvasApi back
// from ChatCanvas.jsx would create a cycle. Same reasoning as ThemeCtx
// living in theme.jsx rather than in ChatCanvas.jsx.
import { createContext } from 'react';

export const CanvasApi = createContext({});
