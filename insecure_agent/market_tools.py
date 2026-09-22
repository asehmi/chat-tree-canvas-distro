# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Financial-analyst tool executors for insecure_agent_provider.py. This
provider has no role-based access gating — every tool here is available
to every user.

Every function here returns a `ToolResult` — the same shape
`insecure_agent/script_runner.py`'s `run_python` already returns — so
insecure_agent_provider.py's tool dispatch can treat every tool identically:
push `block_events` onto the SSE queue, feed `summary_text` back to the
model. Each function catches its own exceptions and folds a failure into
`summary_text` rather than raising, so one bad tool call narrates as a
failure the model can react to, not a dead turn.

IMPORTANT — why `run_full_analysis` exists and the four data-pipeline
functions below it (`scrape_market_data`/`compute_data_metrics`/
`interpret_financials`/`render_chart`) are NOT in TOOL_DEFINITIONS: a model
asked to reproduce a ~250-row JSON blob token-for-token, to pass it between
separate tool calls, can silently drop rows (breaking compute_data_metrics'
>=90-row requirement) or lose the Ticker column, and burns a full extra LLM
round-trip regenerating it each time it's asked to. `run_full_analysis`
avoids that entirely: one atomic, server-orchestrated tool that the model
calls with just a ticker/period/prompt, chaining fetch, indicators, report
generation, and chart rendering as plain Python calls — the DataFrame never
leaves the server process. The four functions still exist (unchanged) as
the internal pipeline stages `run_full_analysis` chains together in
Python; they're just not reachable as separate LLM tool calls.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from insecure_agent.finance import (
    FinancialAnalyst,
    InteractiveFinancialCharts,
    TickerExtractor,
    fetch_market_data,
    is_valid_stock_ticker,
)
from insecure_agent.tool_result import ToolResult

logger = logging.getLogger("insecure_agent.market_tools")

_extractor = TickerExtractor()

# Row cap for chart rendering (Plotly figure JSON sent to the frontend) and
# for scrape_market_data()'s standalone df_json output — run_full_analysis
# itself never serialises data back to the model, so this is purely a
# frontend-payload-size guard now, not a model-context one.
_MAX_DF_ROWS = 500


def _df_from_json(df_json: str) -> pd.DataFrame:
    records = json.loads(df_json)
    df = pd.DataFrame(records)
    if "Date" in df.columns:
        df = df.set_index("Date")
    return df


def _ticker_of(df: pd.DataFrame) -> str:
    return str(df["Ticker"].iloc[0]) if "Ticker" in df.columns else "UNKNOWN"


def _resolve_ticker(text: str) -> str | None:
    """Regex extraction, then a direct-symbol check, then an LLM fallback —
    shared by validate_ticker() and run_full_analysis()."""
    ticker = _extractor.extract_from_text(text)
    if not ticker:
        candidate = text.strip().upper()
        if 1 <= len(candidate) <= 5 and candidate.isalpha() and is_valid_stock_ticker(candidate):
            ticker = candidate
    if not ticker:
        clean = text.strip().replace(" ", "").replace("$", "").replace(".", "")
        if clean.isalpha() and len(text) <= 200:
            ticker = _extractor.get_ticker_from_ai(text)
    return ticker


# ── validate_ticker ───────────────────────────────────────────

def validate_ticker(text: str) -> ToolResult:
    try:
        ticker = _resolve_ticker(text)
        if ticker:
            return ToolResult(summary_text=f"ticker={ticker} valid=True")
        return ToolResult(summary_text=f"No valid ticker found in: {text!r}")
    except Exception as exc:
        logger.warning(f"validate_ticker failed: {exc}")
        return ToolResult(summary_text=f"validate_ticker error: {exc}")


# ── scrape_market_data ────────────────────────────────────────

def _scrape_market_data_df_json(ticker: str, period: str) -> tuple[str, str]:
    """Fetch + compute indicators, returning (df_json, notes_text). Raises
    on failure — callers decide how to report that (scrape_market_data()
    catches it into a ToolResult; run_full_analysis() does its own)."""
    ticker = ticker.upper()
    df = fetch_market_data(ticker, period)

    analyst = FinancialAnalyst(df)
    enriched_df, summary_metrics = analyst.create_technical_analysis()
    market_insights = analyst.generate_market_insights()

    if len(enriched_df) > _MAX_DF_ROWS:
        enriched_df = enriched_df.iloc[-_MAX_DF_ROWS:]

    df_records = enriched_df.reset_index().to_dict(orient="records")
    df_json = json.dumps(df_records, default=str)
    notes = (
        f"Fetched {len(enriched_df)} rows of {ticker} ({period}) with indicators computed.\n"
        f"summary_metrics: {json.dumps(summary_metrics)}\n"
        f"{market_insights}"
    )
    return df_json, notes


def scrape_market_data(ticker: str, period: str = "1y") -> ToolResult:
    """Kept for internal reuse / standalone testing — NOT in TOOL_DEFINITIONS
    (see module docstring). Its df_json summary_text is only meant for a
    caller passing it straight to compute_data_metrics/interpret_financials/
    render_chart in the SAME process, never for an LLM to reproduce."""
    try:
        df_json, notes = _scrape_market_data_df_json(ticker, period)
        summary = (
            f"{notes}\n"
            f"df_json (pass this to compute_data_metrics/interpret_financials/render_chart): {df_json}"
        )
        return ToolResult(summary_text=summary)
    except Exception as exc:
        logger.warning(f"scrape_market_data failed: {exc}")
        return ToolResult(summary_text=f"scrape_market_data error for {ticker!r}: {exc}")


# ── run_full_analysis (the atomic pipeline — see module docstring) ──

def run_full_analysis(ticker: str, period: str = "1y", user_prompt: str = "") -> ToolResult:
    """The atomic pipeline: validate -> fetch+indicators -> data report ->
    charts -> strategic report, all as direct Python calls. df_json is
    created and consumed entirely inside this function — the caller
    (insecure_agent_provider.py's tool_handler) only ever sees the
    ToolResult this returns, never the intermediate data."""
    resolved = _resolve_ticker(ticker)
    if not resolved:
        return ToolResult(summary_text=f"Could not resolve a valid ticker from {ticker!r}.")

    try:
        df_json, fetch_notes = _scrape_market_data_df_json(resolved, period)
    except Exception as exc:
        logger.warning(f"run_full_analysis: data fetch failed: {exc}")
        return ToolResult(summary_text=f"run_full_analysis error fetching data for {resolved!r}: {exc}")

    block_events: list[dict] = []
    notes = [fetch_notes]

    # Stage 2 (technical_analyst, part 1): quantitative data report
    metrics = compute_data_metrics(df_json)
    block_events.extend(metrics.block_events)
    notes.append(metrics.summary_text)
    data_report_text = metrics.block_events[0]["data"]["text"] if metrics.block_events else ""

    # Stage 2 (technical_analyst, part 2): all three charts
    charts = render_chart(df_json)
    block_events.extend(charts.block_events)
    notes.append(charts.summary_text)

    # Stage 3 (financial_analyst): strategic interpretation, conditioned on
    # the data report above and the user's actual question
    prompt = user_prompt or f"Provide a strategic analysis of {resolved}."
    strategic = interpret_financials(df_json, prompt, data_report=data_report_text)
    block_events.extend(strategic.block_events)
    notes.append(strategic.summary_text)

    return ToolResult(block_events=block_events, summary_text="\n".join(notes))


# ── compute_data_metrics ──────────────────────────────────────

def compute_data_metrics(df_json: str) -> ToolResult:
    try:
        df = _df_from_json(df_json)
        ticker = _ticker_of(df)
        analyst = FinancialAnalyst(df)
        report = analyst.generate_financial_data_analysis(ticker)
        return ToolResult(
            block_events=[{"type": "tool_call_result", "data": {"kind": "markdown", "text": report}}],
            summary_text=f"Produced a Market Data Analysis report for {ticker}.",
        )
    except Exception as exc:
        logger.warning(f"compute_data_metrics failed: {exc}")
        return ToolResult(summary_text=f"compute_data_metrics error: {exc}")


# ── interpret_financials ──────────────────────────────────────

def interpret_financials(
    df_json: str, user_prompt: str, data_report: str = "", additional_analysis: str = "",
) -> ToolResult:
    try:
        df = _df_from_json(df_json)
        ticker = _ticker_of(df)
        analyst = FinancialAnalyst(df)
        report = analyst.generate_financial_analysis(
            user_prompt, ticker, data_report=data_report, additional_analysis=additional_analysis,
        )
        return ToolResult(
            block_events=[{"type": "tool_call_result", "data": {"kind": "markdown", "text": report}}],
            summary_text=f"Produced a Market Insights report for {ticker}.",
        )
    except Exception as exc:
        logger.warning(f"interpret_financials failed: {exc}")
        return ToolResult(summary_text=f"interpret_financials error: {exc}")


# ── render_chart ──────────────────────────────────────────────

def render_chart(df_json: str, chart_types: list[str] | None = None) -> ToolResult:
    chart_types = chart_types or ["stock", "indicators", "volume"]
    try:
        df = _df_from_json(df_json)
        if len(df) > _MAX_DF_ROWS:
            df = df.iloc[-_MAX_DF_ROWS:]
        ticker = _ticker_of(df)

        block_events = []
        produced = []
        for chart_type in chart_types:
            try:
                if chart_type == "stock":
                    fig = InteractiveFinancialCharts.create_stock_plot(df)
                    title = f"{ticker} Stock Price"
                elif chart_type == "indicators":
                    fig = InteractiveFinancialCharts.create_technical_indicators_plot(df)
                    title = f"{ticker} RSI / MACD"
                elif chart_type == "volume":
                    fig = InteractiveFinancialCharts.create_volume_analysis_plot(df)
                    title = f"{ticker} Volume"
                else:
                    continue
                block_events.append({
                    "type": "tool_call_result",
                    "data": {"kind": "chart", "title": title, "figure_json": fig.to_json()},
                })
                produced.append(chart_type)
            except Exception as exc:
                logger.warning(f"render_chart failed for {chart_type}: {exc}")

        if not produced:
            return ToolResult(summary_text=f"render_chart produced no charts for {ticker}.")
        return ToolResult(
            block_events=block_events,
            summary_text=f"Rendered {len(produced)} chart(s) for {ticker}: {', '.join(produced)}.",
        )
    except Exception as exc:
        logger.warning(f"render_chart failed: {exc}")
        return ToolResult(summary_text=f"render_chart error: {exc}")


# ── search ────────────────────────────────────────────────────

def _polygon_news(ticker: str, max_results: int) -> list[dict]:
    import os

    import httpx

    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        return []
    url = (
        f"https://api.polygon.io/v2/reference/news"
        f"?ticker={ticker.upper()}&limit={max_results}&order=desc&apiKey={api_key}"
    )
    with httpx.Client(timeout=10) as client:
        response = client.get(url)
        response.raise_for_status()
    return [
        {
            "title": r.get("title", ""),
            "description": r.get("description", ""),
            "url": r.get("article_url", ""),
            "source": "polygon",
        }
        for r in response.json().get("results", [])
    ]


def _duckduckgo_search(query: str, max_results: int) -> list[dict]:
    try:
        try:
            from ddgs import DDGS  # duckduckgo_search was renamed to ddgs
        except ImportError:
            from duckduckgo_search import DDGS
        results = DDGS().text(query, max_results=max_results)
        return [
            {"title": r.get("title", ""), "description": r.get("body", ""), "url": r.get("href", ""), "source": "duckduckgo"}
            for r in (results or [])
        ]
    except ImportError:
        logger.warning("ddgs/duckduckgo-search not installed; search fallback unavailable")
        return []
    except Exception as exc:
        logger.warning(f"DuckDuckGo search failed: {exc}")
        return []


def search(query: str, max_results: int = 5) -> ToolResult:
    try:
        articles: list[dict] = []
        try:
            articles = _polygon_news(query, max_results)
        except Exception as exc:
            logger.debug(f"Polygon news failed: {exc}")

        if not articles:
            articles = _duckduckgo_search(query, max_results)

        if not articles:
            return ToolResult(summary_text=f"No results for {query!r}.")

        lines = [f"- {a['title']}: {a['description'][:200]} ({a['url']})" for a in articles]
        return ToolResult(summary_text=f"{len(articles)} result(s) for {query!r}:\n" + "\n".join(lines))
    except Exception as exc:
        logger.warning(f"search failed: {exc}")
        return ToolResult(summary_text=f"search error: {exc}")


# ── ask_user ──────────────────────────────────────────────────

def ask_user(question: str) -> ToolResult:
    """Returns the `question`-block payload. Ending the round after this
    tool runs is insecure_agent_provider.py's job (it raises EndTurn once
    this returns) — this function stays a plain ToolResult producer like
    every other tool, for uniform dispatch."""
    return ToolResult(
        block_events=[{"type": "tool_call_result", "data": {"kind": "question", "text": question}}],
    )
