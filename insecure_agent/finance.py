# Copyright (c) 2026 Arvindra Sehmi
# Licensed under the MIT License — see LICENSE for details.

"""Financial-analyst domain logic for insecure_agent's market tools:
ticker resolution, market-data fetch (yfinance, or Polygon.io first if
POLYGON_API_KEY is set), technical-indicator math, LLM-written analysis
reports, and Plotly chart builders.

Fully synchronous throughout: yfinance/pandas are sync-native, and every
tool in this provider runs inside a plain synchronous `tool_handler`
callback (see insecure_agent_provider.py's `_run_agent`), so there's no
async event loop to bridge into. Ticker/data lookups use a simple
`functools.lru_cache` — a session-lifetime cache, cleared on restart, which
is fine for a local demo. LLM calls go through this project's own
`insecure_agent.llm.get_llm_client()`, uniform across Anthropic, Gemini,
and OpenRouter. Report generation is a single whole-response call, not
token-streamed — consistent with how `run_python` results already surface
as complete blocks, not sub-streamed. No role-based access gating — every
tool here is available to every user of this provider.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
from functools import lru_cache
from textwrap import dedent

import httpx
import pandas as pd

from insecure_agent.llm import get_llm_client

logger = logging.getLogger("insecure_agent.finance")

# ── Colour palette ────────────────────────────────────────────
RED = "#dc143c"
BLUE = "#003fff"
GREEN = "#00a86b"
ORANGE = "#ff8427"
PURPLE = "#820263"

PERIOD_TO_BUSINESS_DAYS: dict[str, int | None] = {
    "1d": 1, "5d": 5, "1mo": 22, "3mo": 66, "6mo": 132, "ytd": 252,
    "1y": 252, "2y": 504, "3y": 756, "5y": 1260, "10y": 2520, "max": None,
}


def _strip_think_blocks(text: str) -> str:
    """Remove <think>...</think> blocks some reasoning models emit."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


# ── yfinance helpers ──────────────────────────────────────────

@lru_cache(maxsize=64)
def get_yfinance_stock_ticker(ticker: str):
    import yfinance as yf
    return yf.Ticker(ticker.upper())


@lru_cache(maxsize=64)
def is_valid_yfinance_stock_ticker(ticker: str) -> bool:
    try:
        stock = get_yfinance_stock_ticker(ticker)
        info = stock.info
        return bool(info and info.get("quoteType") == "EQUITY" and info.get("symbol") == ticker.upper())
    except Exception:
        return False


def get_yfinance_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    import yfinance as yf
    stock = get_yfinance_stock_ticker(ticker)
    days = PERIOD_TO_BUSINESS_DAYS.get(period)
    if days is None:
        df = stock.history(period="max")
    else:
        business_days = int(days * 1.1)
        today = dt.datetime.today()
        date_range = pd.date_range(end=today, periods=business_days, freq="B")
        start_date = date_range[0].strftime("%Y-%m-%d")
        df = stock.history(start=start_date)
    df.index.name = "Date"
    df.index = pd.to_datetime(df.index).strftime("%Y-%m-%d")
    df["Ticker"] = ticker
    df["Period"] = period
    return df


# ── Polygon.io helpers (optional — POLYGON_API_KEY unset is a no-op) ──

def is_valid_polygon_stock_ticker(ticker: str) -> bool:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not configured")
    url = (
        f"https://api.polygon.io/v3/reference/tickers"
        f"?ticker={ticker}&active=true&limit=1&apiKey={api_key}"
    )
    with httpx.Client(timeout=10) as client:
        response = client.get(url)
        response.raise_for_status()
    results = response.json().get("results", [])
    return bool(results and results[0]["ticker"] == ticker)


def get_polygon_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    api_key = os.getenv("POLYGON_API_KEY")
    if not api_key:
        raise RuntimeError("POLYGON_API_KEY not configured — use yfinance fallback")
    if not is_valid_polygon_stock_ticker(ticker):
        raise RuntimeError(f"Invalid stock ticker: {ticker}")

    days = PERIOD_TO_BUSINESS_DAYS.get(period) or PERIOD_TO_BUSINESS_DAYS["5y"]
    business_days = int(days * 1.1)
    today = dt.datetime.today()
    date_range = pd.date_range(end=today, periods=business_days, freq="B")
    start_date = date_range[0].strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day"
        f"/{start_date}/{end_date}?adjusted=true&sort=asc&apiKey={api_key}"
    )
    with httpx.Client(timeout=15) as client:
        response = client.get(url)
        response.raise_for_status()

    df = pd.DataFrame(response.json()["results"])
    df = df.rename(columns={"o": "Open", "c": "Close", "l": "Low", "h": "High", "v": "Volume", "t": "Timestamp"})
    df["Date"] = pd.to_datetime(df["Timestamp"], unit="ms").dt.strftime("%Y-%m-%d")
    df = df.drop(columns=["Timestamp"], errors="ignore")
    df = df[["Date", "Open", "Close", "Low", "High", "Volume"]]
    df = df.sort_values("Date").set_index("Date")
    df["Ticker"] = ticker
    df["Period"] = period
    return df


def is_valid_stock_ticker(ticker: str) -> bool:
    """Validate ticker: tries Polygon.io first (if configured), falls back to yfinance."""
    try:
        if is_valid_polygon_stock_ticker(ticker):
            return True
    except Exception:
        pass
    return is_valid_yfinance_stock_ticker(ticker)


def fetch_market_data(ticker: str, period: str = "1y") -> pd.DataFrame:
    """Fetch OHLCV data: tries Polygon.io (if configured), falls back to yfinance."""
    try:
        return get_polygon_data(ticker, period)
    except Exception as poly_err:
        logger.info(f"Polygon.io unavailable ({poly_err}), using yfinance")
        try:
            return get_yfinance_data(ticker, period)
        except Exception as yf_err:
            raise RuntimeError(f"Both data sources failed for {ticker}: polygon={poly_err}, yfinance={yf_err}")


# ── TickerExtractor ───────────────────────────────────────────

class TickerExtractor:
    """Extract a stock ticker from free text using regex patterns + LLM fallback."""

    _PATTERNS = [
        r"\b([A-Z]{1,5})\b(?=\s+(?:stock|share|shares|price|prices|ticker|equity|market))",
        r"\$([A-Z]{1,5})(?!\d)",
        r"\b(?:ticker|symbol)[:\s]+([A-Z]{1,5})\b",
        r"\b([A-Z]{1,5})\b(?=\s+(?:inc|corp|company))",
    ]

    def extract_from_text(self, text: str) -> str | None:
        for pattern in self._PATTERNS:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                symbol = (match[0] if isinstance(match, tuple) else match).upper()
                try:
                    if is_valid_stock_ticker(symbol):
                        return symbol
                except Exception:
                    continue
        return None

    def get_ticker_from_ai(self, text: str) -> str | None:
        """AI-fallback ticker extraction — uses this provider's own configured
        LLM client (whatever LLM_PROVIDER is set to), not a separate cheap
        "router" model like the source uses. A simplification: this is a
        low-stakes, low-volume lookup for a demo, not worth a second
        provider config."""
        try:
            suggested = get_llm_client().generate_response(
                "You are a financial expert. Extract or suggest stock tickers from text.",
                (
                    "Extract a stock ticker symbol from this text. Return ONLY the "
                    f"ticker symbol, nothing else.\n\nText: {text}"
                ),
            )
            suggested = str(suggested).strip().upper()
            if suggested and is_valid_stock_ticker(suggested):
                return suggested
        except Exception as exc:
            logger.warning(f"AI ticker extraction failed: {exc}")
        return None


# ── FinancialAnalyst ──────────────────────────────────────────

class FinancialAnalyst:
    """Wraps a pre-fetched OHLCV DataFrame, computes indicators, and
    generates LLM-driven analysis reports.

    The DataFrame passed in should already have OHLCV columns + 'Ticker' +
    'Period'. Call create_technical_analysis() first to add indicator
    columns, or pass an already-enriched DataFrame.
    """

    _SYSTEM_PROMPT = dedent("""
        You are a sophisticated financial expert assistant. Analyse the provided metrics and generate insights about:
        1. Current market position based on technical indicators
        2. Volume analysis and its implications
        3. Trend strength and potential reversal points
        4. Risk assessment based on volatility metrics
        5. Specific trading signals from indicators

        Format your response with clear sections and bullet points when appropriate.
    """)

    def __init__(self, df: pd.DataFrame) -> None:
        assert df is not None and not df.empty, "DataFrame must be non-empty."
        # Ticker/Period may be missing if a model reconstructs df_json instead
        # of copying scrape_market_data's output verbatim (the tool contract
        # asks it to, but nothing enforces that) — default rather than raise,
        # so a malformed pass-through degrades to an "UNKNOWN"-ticker report
        # instead of crashing the whole tool call.
        self.ticker: str = str(df["Ticker"].iloc[0]) if "Ticker" in df.columns else "UNKNOWN"
        self.period: str = str(df["Period"].iloc[0]) if "Period" in df.columns else ""
        self.df = df.copy()
        self.summary_metrics: dict = {}

    # ── Static indicator calculators ─────────────────────────

    @staticmethod
    def calculate_rsi(prices: pd.Series, period: int = 14) -> pd.Series:
        delta = prices.diff()
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def calculate_macd(prices: pd.Series, slow: int = 26, fast: int = 12, signal: int = 9) -> tuple[pd.Series, pd.Series]:
        exp1 = prices.ewm(span=fast).mean()
        exp2 = prices.ewm(span=slow).mean()
        macd = exp1 - exp2
        return macd, macd.ewm(span=signal).mean()

    @staticmethod
    def calculate_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        hl = df["High"] - df["Low"]
        hc = abs(df["High"] - df["Close"].shift())
        lc = abs(df["Low"] - df["Close"].shift())
        return pd.concat([hl, hc, lc], axis=1).max(axis=1).rolling(window=period).mean()

    @staticmethod
    def calculate_bollinger_bands(prices: pd.Series, period: int = 20, std_dev: int = 2) -> tuple[pd.Series, pd.Series]:
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()
        return sma + std * std_dev, sma - std * std_dev

    @staticmethod
    def calculate_summary_metrics(df: pd.DataFrame) -> dict:
        last_close = float(df["Close"].iloc[-1])
        prev_close = float(df["Close"].iloc[0])
        change = last_close - prev_close
        return {
            "last_close": round(last_close, 4),
            "change": round(change, 4),
            "pct_change": round((change / prev_close) * 100, 2),
            "high": round(float(df["High"].max()), 4),
            "low": round(float(df["Low"].min()), 4),
            "volume": int(df["Volume"].sum()),
        }

    # ── Technical analysis ────────────────────────────────────

    def create_technical_analysis(self) -> tuple[pd.DataFrame, dict]:
        """Compute all indicators in-place and return (enriched_df, summary_metrics)."""
        close = self.df["Close"]
        volume = self.df["Volume"]

        self.df["SMA_20"] = close.rolling(20).mean()
        self.df["SMA_50"] = close.rolling(50).mean()
        self.df["RSI"] = self.calculate_rsi(close)
        self.df["MACD"], self.df["Signal"] = self.calculate_macd(close)
        self.df["Volume_SMA_20"] = volume.rolling(20).mean()
        self.df["Volume_SMA_50"] = volume.rolling(50).mean()
        self.df["Volume_ratio"] = volume / self.df["Volume_SMA_20"]
        self.df["ATR"] = self.calculate_atr(self.df)
        self.df["Bollinger_Upper"], self.df["Bollinger_Lower"] = self.calculate_bollinger_bands(close)
        self.df["Daily_Return"] = close.pct_change()
        self.df["Volatility"] = self.df["Daily_Return"].rolling(20).std()

        self.summary_metrics = self.calculate_summary_metrics(self.df)
        return self.df, self.summary_metrics

    def analyze_volume_patterns(self) -> dict:
        close = self.df["Close"]
        volume = self.df["Volume"]
        vol_mean, vol_std = volume.mean(), volume.std()
        unusual = self.df[volume > (vol_mean + 2 * vol_std)]
        self.df["price_change"] = close.pct_change()

        events = []
        for date in unusual.index:
            try:
                events.append({
                    "date": str(date),
                    "volume": int(unusual.loc[date, "Volume"]),
                    "price_change": float(self.df.loc[date, "price_change"]) if not pd.isna(self.df.loc[date, "price_change"]) else 0.0,
                })
            except Exception:
                continue

        return {
            "average_volume": float(volume.mean()),
            "volume_trend": "increasing" if self.df["Volume_SMA_20"].iloc[-1] > self.df["Volume_SMA_50"].iloc[-1] else "decreasing",
            "high_volume_days": [str(d) for d in self.df[self.df["Volume_ratio"] > 2].index.tolist()],
            "volume_price_correlation": float(volume.corr(close)),
            "unusual_volume_events": events,
        }

    def analyze_technical_signals(self) -> dict:
        latest = self.df.iloc[-1]
        prev = self.df.iloc[-2]
        return {
            "rsi_signal": "oversold" if latest["RSI"] < 30 else "overbought" if latest["RSI"] > 70 else "neutral",
            "macd_signal": (
                "bullish" if latest["MACD"] > latest["Signal"] and prev["MACD"] <= prev["Signal"]
                else "bearish" if latest["MACD"] < latest["Signal"] and prev["MACD"] >= prev["Signal"]
                else "neutral"
            ),
            "ma_signal": "bullish" if latest["SMA_20"] > latest["SMA_50"] else "bearish",
            "volatility_state": "high" if latest["Volatility"] > self.df["Volatility"].mean() * 1.5 else "normal",
            "volume_signal": "high" if latest["Volume"] > latest["Volume_SMA_20"] * 1.5 else "normal",
            "price_to_sma20": float((latest["Close"] / latest["SMA_20"] - 1) * 100),
            "bollinger_position": float(
                (latest["Close"] - latest["Bollinger_Lower"])
                / (latest["Bollinger_Upper"] - latest["Bollinger_Lower"])
            ),
        }

    def generate_market_insights(self) -> str:
        signals = self.analyze_technical_signals()
        vol = self.analyze_volume_patterns()
        returns = float(self.df["Daily_Return"].iloc[-20:].mean() * 100)
        volatility = float(self.df["Volatility"].iloc[-1] * 100)

        return dedent(f"""
            ---

            **Price Action, Volume and Risk Insights for {self.ticker}**:

            Technical Indicators:
            - RSI ({self.df['RSI'].iloc[-1]:.2f}) indicates {signals['rsi_signal']} conditions
            - MACD shows {signals['macd_signal']} momentum
            - Price is {signals['price_to_sma20']:.2f}% relative to 20-day SMA
            - Bollinger Band position: {signals['bollinger_position']:.2f} (0=lower, 1=upper band)

            Volume Analysis:
            - Average Volume: {vol['average_volume']:,.0f}
            - Volume Trend: {vol['volume_trend']}
            - Volume-Price Correlation: {vol['volume_price_correlation']:.2f}
            - Current Volume Signal: {signals['volume_signal']}

            Risk Metrics:
            - 20-day Rolling Volatility: {volatility:.2f}%
            - 20-day Average Return: {returns:.2f}%
            - ATR: {self.df['ATR'].iloc[-1]:.2f}
            - Volatility State: {signals['volatility_state']}

            ---
        """)

    @staticmethod
    def _get_financial_analysis_data(df: pd.DataFrame, max_rows: int = 90) -> str:
        """Close/Volume/RSI/MACD as a markdown table for the LLM prompt,
        capped at the most recent max_rows to keep prompt size manageable."""
        subset = df[["Close", "Volume", "RSI", "MACD"]].copy()
        if len(subset) > max_rows:
            subset = subset.iloc[-max_rows:]
        subset = subset.reset_index()
        subset["Date"] = pd.to_datetime(subset["Date"]).dt.strftime("%Y-%m-%d")
        subset["Close"] = subset["Close"].map("{:.2f}".format)
        subset["RSI"] = subset["RSI"].map("{:.2f}".format)
        subset["MACD"] = subset["MACD"].map("{:.2f}".format)
        subset = subset.set_index("Date")
        return subset.to_markdown(tablefmt="grid")

    # ── LLM-driven analysis (whole-response, not streamed) ────

    def generate_financial_data_analysis(self, ticker: str) -> str:
        """A 'Market Data Analysis' report (requires >=90 rows of data)."""
        if len(self.df) < 90:
            return "Insufficient data for financial data analysis. Select a longer time period."

        table_md = self._get_financial_analysis_data(self.df)
        prompt = dedent(f"""
            Below is the {ticker} stock raw market data series for ["Close", "Volume", "RSI", "MACD"]. I would like you to:

            <INSTRUCTIONS>
            1. Create a markdown report with the title "### Market Data Analysis for {ticker}".
            2. The report should contain insights based only on these **specific** data series:
               - ["Close", "Volume", "RSI", "MACD"]
            3. Highlight months of interest for early trends, sharp declines, recoveries, and other key turning points.
            4. Provide a detailed examination of the activity in recent months.
            5. Provide other relevant observations supported by the data.
            </INSTRUCTIONS>

            {table_md}
        """)
        text = get_llm_client().generate_response(self._SYSTEM_PROMPT, prompt)
        return _strip_think_blocks(str(text))

    def generate_financial_analysis(
        self,
        chat_prompt: str,
        ticker: str,
        data_report: str = "",
        additional_analysis: str = "",
        include_analysis: bool = True,
    ) -> str:
        """A strategic 'Market Insights' report. `data_report` (typically
        compute_data_metrics's prior output) is folded in as prompt context
        instead of the source's multi-turn conversation_history — this
        provider makes one whole-response LLM call per tool, not a running
        chat session per analyst instance."""
        prompt = ""
        if include_analysis:
            if len(chat_prompt.split()) >= 5:
                prompt = f"{chat_prompt}\n\n"
            insights = self.generate_market_insights()
            prompt += dedent(f"""
                Produce a report with detailed comments and analysis of the following
                summary market insights data for {ticker} stock:

                <INSTRUCTIONS>
                - Create a markdown report with the title "### Market Insights for {ticker}".
                - The report should contain comments based on the summary market insights data.
                </INSTRUCTIONS>

                {insights}
            """)
            if additional_analysis:
                prompt += f"\nIn addition to the above analysis, please:\n{additional_analysis}"

        if not prompt:
            prompt = chat_prompt or f"Tell me what you know about the {ticker} stock."

        if data_report:
            prompt = f"Prior analysis for context:\n\n{data_report}\n\n---\n\n{prompt}"

        text = get_llm_client().generate_response(self._SYSTEM_PROMPT, prompt)
        return _strip_think_blocks(str(text))


# ── InteractiveFinancialCharts ────────────────────────────────

class InteractiveFinancialCharts:
    """`plotly` is imported lazily inside each method (not at module top) so
    the rest of this module — indicator math, ticker/data fetching — stays
    importable and testable without `plotly` installed."""

    @staticmethod
    def create_stock_plot(df: pd.DataFrame) -> "go.Figure":
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots

        df = df.copy()
        df.index = pd.to_datetime(df.index)
        if df.index.tz:
            df.index = df.index.tz_localize(None)
        ticker = df["Ticker"].iloc[0] if "Ticker" in df.columns else "UNKNOWN"

        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["Open"], high=df["High"], low=df["Low"], close=df["Close"],
            increasing_line_color=GREEN, decreasing_line_color=RED, name="Price",
        ), secondary_y=False)
        fig.add_trace(go.Scatter(x=df.index, y=df["SMA_20"], mode="lines", name="SMA 20", line=dict(color=ORANGE)), secondary_y=False)
        fig.add_trace(go.Scatter(x=df.index, y=df["SMA_50"], mode="lines", name="SMA 50", line=dict(color=BLUE)), secondary_y=False)
        vol_colors = [GREEN if c >= o else RED for c, o in zip(df["Close"], df["Open"])]
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, name="Volume", opacity=0.5, visible="legendonly"), secondary_y=True)
        fig.update_layout(
            title=f"{ticker} Stock Price Candles", xaxis_title="Date", yaxis_title="Price ($)",
            xaxis_rangeslider_visible=False, legend_title="Legend",
        )
        fig.update_yaxes(title_text="Price ($)", secondary_y=False)
        fig.update_yaxes(title_text="Volume", secondary_y=True)
        return fig

    @staticmethod
    def create_technical_indicators_plot(df: pd.DataFrame) -> "go.Figure":
        import plotly.graph_objects as go

        df = df.copy()
        df.index = pd.to_datetime(df.index)
        ticker = df["Ticker"].iloc[0] if "Ticker" in df.columns else "UNKNOWN"

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=df.index, y=df["RSI"], mode="lines", name="RSI", line=dict(color=PURPLE), legendgroup="RSI"))
        fig.add_trace(go.Scatter(x=[df.index.min(), df.index.max()], y=[70, 70], mode="lines", line=dict(color=RED, dash="dash", width=1), name="Overbought (70)", showlegend=False, legendgroup="RSI"))
        fig.add_trace(go.Scatter(x=[df.index.min(), df.index.max()], y=[30, 30], mode="lines", line=dict(color=GREEN, dash="dash", width=1), name="Oversold (30)", showlegend=False, legendgroup="RSI"))
        fig.add_trace(go.Scatter(
            x=df.index.tolist() + df.index.tolist()[::-1],
            y=df["RSI"].where(df["RSI"] <= 30, 30).tolist() + [30] * len(df),
            fill="toself", fillcolor=GREEN, opacity=0.2, mode="none", hoverinfo="skip", showlegend=False, legendgroup="RSI", name="Below 30",
        ))
        fig.add_trace(go.Scatter(
            x=df.index.tolist() + df.index.tolist()[::-1],
            y=df["RSI"].where(df["RSI"] >= 70, 70).tolist() + [70] * len(df),
            fill="toself", fillcolor=RED, opacity=0.2, mode="none", hoverinfo="skip", showlegend=False, legendgroup="RSI", name="Above 70",
        ))
        fig.add_trace(go.Scatter(x=df.index, y=df["MACD"], mode="lines", name="MACD", line=dict(color=BLUE)))
        fig.add_trace(go.Scatter(x=df.index, y=df["Signal"], mode="lines", name="Signal", line=dict(color=ORANGE)))
        fig.add_trace(go.Bar(
            x=df.index, y=df["MACD"] - df["Signal"],
            marker_color=[GREEN if v >= 0 else RED for v in df["MACD"] - df["Signal"]],
            name="Histogram",
        ))
        fig.update_layout(title=f"{ticker} RSI and MACD", xaxis_title="Date", yaxis_title="Value", legend_title="Legend")
        return fig

    @staticmethod
    def create_volume_analysis_plot(df: pd.DataFrame) -> "go.Figure":
        import plotly.graph_objects as go

        df = df.copy()
        df.index = pd.to_datetime(df.index)
        ticker = df["Ticker"].iloc[0] if "Ticker" in df.columns else "UNKNOWN"
        vol_colors = [GREEN if c >= o else RED for c, o in zip(df["Close"], df["Open"])]
        fig = go.Figure()
        fig.add_trace(go.Bar(x=df.index, y=df["Volume"], marker_color=vol_colors, name="Volume"))
        fig.add_trace(go.Scatter(x=df.index, y=df["Volume"].rolling(20).mean(), mode="lines", name="Volume MA (20)", line=dict(color=BLUE)))
        fig.update_layout(title=f"{ticker} Volume Analysis", xaxis_title="Date", yaxis_title="Volume", legend_title="Legend")
        return fig
