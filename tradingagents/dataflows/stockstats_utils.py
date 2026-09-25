import contextlib
import functools
import logging
import os
import threading
import time
from collections.abc import Iterable
from typing import Annotated

import pandas as pd
import yfinance as yf
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
    sunday_to_monday,
)
from pandas.tseries.offsets import CustomBusinessDay
from stockstats import wrap
from yfinance.exceptions import YFRateLimitError

from .config import get_config
from .symbol_utils import NoMarketDataError, crypto_base, normalize_symbol
from .utils import safe_ticker_component

logger = logging.getLogger(__name__)

# A vendor's latest OHLCV row this many calendar days before the requested date
# is treated as stale. Generous enough to span long holiday weekends, tight
# enough to catch the year-old frames yfinance occasionally returns (#1021).
MAX_OHLCV_STALE_DAYS = 10

# How long a same-day cache that does not yet reach the requested day may be
# reused before it is refetched (#1150). Short enough that an intraday run picks
# up today's close soon after it publishes, long enough that a day with no bar
# at all (weekend, holiday) cannot trigger a download on every call.
OHLCV_CACHE_TTL_SECONDS = 900

# Trailing window scanned for sessions the vendor silently dropped. Covers the
# spans of the short and medium indicators (10 EMA, RSI, MACD, 50 SMA), where a
# single missing bar moves the values most.
MISSING_SESSION_LOOKBACK_DAYS = 120

# Largest disagreement tolerated between the daily/hourly price scales of the
# sessions either side of a rebuilt bar. A dividend between them moves it by the
# yield; a split moves it far more, and then the bar is not rebuilt.
REBUILD_MAX_SCALE_SPREAD = 0.02


class _NYSEHolidayCalendar(AbstractHolidayCalendar):
    """Full-day NYSE closures, used only to spot sessions missing from vendor data."""

    rules = [
        # NYSE does not close on Friday when New Year's Day falls on a Saturday.
        Holiday("New Year's Day", month=1, day=1, observance=sunday_to_monday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-01-01", observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
        # Unscheduled closures.
        Holiday("National Day of Mourning (Carter)", year=2025, month=1, day=9),
    ]


@functools.cache
def _nyse_session() -> CustomBusinessDay:
    return CustomBusinessDay(calendar=_NYSEHolidayCalendar())


def yf_retry(func, max_retries=3, base_delay=2.0):
    """Execute a yfinance call with exponential backoff on rate limits.

    yfinance raises YFRateLimitError on HTTP 429 responses but does not
    retry them internally. This wrapper adds retry logic specifically
    for rate limits. Other exceptions propagate immediately.
    """
    for attempt in range(max_retries + 1):
        try:
            return func()
        except YFRateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(f"Yahoo Finance rate limited, retrying in {delay:.0f}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(delay)
            else:
                raise


def _ensure_date_column(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize the date column to ``Date``.

    Some yfinance builds leave the index unnamed (so ``reset_index()`` yields
    ``index``) or use ``Datetime`` for intraday data. Rename the first
    date-like column so indicators don't silently drop when it isn't ``Date``.
    """
    if "Date" in data.columns:
        return data
    for candidate in ("index", "Datetime", "date"):
        if candidate in data.columns:
            return data.rename(columns={candidate: "Date"})
    return data


def _clean_dataframe(data: pd.DataFrame) -> pd.DataFrame:
    """Normalize a stock DataFrame for stockstats: parse dates, drop invalid rows, fill price gaps."""
    data = _ensure_date_column(data)
    data["Date"] = pd.to_datetime(data["Date"], errors="coerce")
    data = data.dropna(subset=["Date"])

    price_cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in data.columns]
    data[price_cols] = data[price_cols].apply(pd.to_numeric, errors="coerce")
    data = data.dropna(subset=["Close"])
    data[price_cols] = data[price_cols].ffill().bfill()

    return data


def _coerce_ohlcv_dates(data: pd.DataFrame) -> pd.Series:
    """Return parsed dates from an OHLCV frame, whether Date is a column or the index."""
    if "Date" in data.columns:
        return pd.to_datetime(data["Date"], errors="coerce").dropna()
    # yfinance keeps the dates in the index (a DatetimeIndex, sometimes unnamed).
    if isinstance(data.index, pd.DatetimeIndex):
        return pd.Series(pd.to_datetime(data.index, errors="coerce")).dropna()
    # Fallback: expose the index and look for any date-like column.
    df = data.reset_index()
    for col in ("Date", "Datetime", "date", "index"):
        if col in df.columns:
            parsed = pd.to_datetime(df[col], errors="coerce").dropna()
            if not parsed.empty:
                return parsed
    return pd.Series(dtype="datetime64[ns]")


def _assert_ohlcv_not_stale(
    data: pd.DataFrame,
    curr_date: str,
    symbol: str,
    canonical: str | None = None,
    *,
    max_stale_days: int = MAX_OHLCV_STALE_DAYS,
) -> None:
    """Reject OHLCV whose latest row is far older than curr_date.

    Raises NoMarketDataError (with a stale-specific detail) so the router treats
    it like any other "no usable data from this vendor" — try the next vendor,
    then emit one clear unavailable signal. Empty frames are left to the
    caller's existing no-data handling; this guards only the dangerous case of
    present-but-stale rows (a vendor returning a year-old frame that would
    otherwise feed wrong prices to the agent, #1021).
    """
    if data is None or data.empty:
        return
    requested = pd.to_datetime(curr_date, errors="coerce")
    if pd.isna(requested):
        return
    requested = requested.normalize()
    dates = _coerce_ohlcv_dates(data)
    if dates.empty:
        return
    latest = dates.max().normalize()
    stale_days = (requested - latest).days
    if stale_days > max_stale_days:
        raise NoMarketDataError(
            symbol,
            canonical,
            f"latest row is {latest.date()}, {stale_days} days before the "
            f"requested {requested.date()} (stale) — refusing to use it",
        )


def find_missing_sessions(
    dates: Iterable,
    canonical: str,
    *,
    lookback_days: int = MISSING_SESSION_LOOKBACK_DAYS,
) -> list[str]:
    """US trading sessions absent from the recent span of ``dates``, as YYYY-MM-DD.

    Yahoo intermittently serves an empty bar for a ticker, sometimes for hours,
    and yfinance drops empty rows by default, so a frame can arrive a session
    short with nothing marking the hole (OUST lost 2026-09-22 this way). Only
    sessions strictly inside the covered span count: the newest bar may not be
    published yet, and a far-stale frame is ``_assert_ohlcv_not_stale``'s job.
    Crypto, futures, forex, indices and non-US listings follow other calendars
    and are not checked.
    """
    if crypto_base(canonical) is not None or any(c in canonical for c in ".=^"):
        return []
    have = pd.DatetimeIndex(pd.to_datetime(pd.Series(list(dates)), errors="coerce").dropna())
    if have.empty:
        return []
    if have.tz is not None:
        have = have.tz_localize(None)
    have = have.normalize()
    last = have.max()
    first = max(have.min(), last - pd.Timedelta(days=lookback_days))
    sessions = pd.date_range(first, last, freq=_nyse_session())
    return [d.strftime("%Y-%m-%d") for d in sessions.difference(have)]


def _rebuild_sessions(frame: pd.DataFrame, canonical: str, missing: list[str]) -> pd.DataFrame:
    """Daily bars for ``missing`` rebuilt from Yahoo 1h bars, scaled to ``frame``.

    Hourly bars survive the daily-feed glitches ``find_missing_sessions`` spots.
    Per session: first open, highest high, lowest low, last close, summed
    volume. They are unadjusted and miss auction prints, so each rebuilt bar is
    scaled by the daily/hourly ratio of the nearest intact session on each side;
    when the two sides disagree (a split between them) the session is left
    missing rather than guessed. Checked against 696 official bars, rebuilt
    closes were within 0.1% for 9 in 10 and within 1% for all, and opens,
    highs and lows within 0.2% for 9 in 10; volume is an estimate (median 6%
    off, more on auction-heavy days). Returns only the sessions rebuilt,
    flagged ``Rebuilt=True``.
    """
    days = pd.DatetimeIndex(pd.to_datetime(missing))
    try:
        hourly = yf_retry(lambda: yf.Ticker(canonical).history(
            start=(days.min() - pd.Timedelta(days=10)).strftime("%Y-%m-%d"),
            end=(days.max() + pd.Timedelta(days=11)).strftime("%Y-%m-%d"),
            interval="1h",
            auto_adjust=False,
            prepost=False,
        ))
    except Exception as exc:  # noqa: BLE001 — rebuilding is best-effort; the gap stays flagged
        logger.warning("%s: could not fetch hourly bars to rebuild %s: %s", canonical, missing, exc)
        return pd.DataFrame()
    if hourly is None or hourly.empty:
        return pd.DataFrame()

    idx = hourly.index
    if idx.tz is not None:
        idx = idx.tz_convert("America/New_York")
    hourly_days = hourly.groupby(pd.to_datetime(idx.date)).agg(
        Open=("Open", "first"), High=("High", "max"), Low=("Low", "min"),
        Close=("Close", "last"), Volume=("Volume", "sum"),
    ).dropna(subset=["Open", "High", "Low", "Close"])
    ex_dividend_days = set()
    if "Dividends" in hourly.columns:
        paid = (hourly["Dividends"].fillna(0) != 0).to_numpy()
        ex_dividend_days = set(pd.to_datetime(idx[paid].date))

    daily_dates = pd.to_datetime(frame["Date"])
    if daily_dates.dt.tz is not None:
        daily_dates = daily_dates.dt.tz_localize(None)
    daily = frame.set_index(daily_dates.dt.normalize())
    intact = daily.index.intersection(hourly_days.index)

    rows = []
    for day in days:
        before, after = intact[intact < day], intact[intact > day]
        if day not in hourly_days.index or before.empty or after.empty:
            continue
        prev, nxt = before[-1], after[0]
        sides = [prev, nxt]
        price_scale = daily.loc[sides, "Close"] / hourly_days.loc[sides, "Close"]
        if price_scale.max() / price_scale.min() - 1 > REBUILD_MAX_SCALE_SPREAD:
            continue
        volume_scale = (daily.loc[sides, "Volume"] / hourly_days.loc[sides, "Volume"]).where(
            hourly_days.loc[sides, "Volume"] > 0
        ).mean()
        bar = hourly_days.loc[day]
        # The daily feed adjusts every row before an ex-dividend date, so a day
        # on or after an ex-date between the sides shares the later side's
        # scale, and a day before one the earlier side's.
        on_or_after_ex = any(prev < d <= day for d in ex_dividend_days)
        before_ex = any(day < d <= nxt for d in ex_dividend_days)
        if on_or_after_ex and not before_ex:
            scale = price_scale[nxt]
        elif before_ex and not on_or_after_ex:
            scale = price_scale[prev]
        else:
            scale = price_scale.mean()
        volume = bar["Volume"] * (volume_scale if pd.notna(volume_scale) else 1)
        rows.append({
            "Date": day,
            **{c: bar[c] * scale for c in ("Open", "High", "Low", "Close")},
            "Volume": int(round(volume)),
            "Rebuilt": True,
        })
    return pd.DataFrame(rows)


def fill_missing_sessions(frame: pd.DataFrame, canonical: str) -> pd.DataFrame:
    """Rebuild sessions Yahoo's daily feed dropped; rebuilt rows get ``Rebuilt=True``.

    ``frame`` has a ``Date`` column. Sessions that can't be rebuilt stay
    missing, are logged here, and are flagged downstream via
    ``find_missing_sessions``.
    """
    missing = find_missing_sessions(frame["Date"], canonical)
    if not missing:
        return frame
    rebuilt = _rebuild_sessions(frame, canonical, missing)
    if not rebuilt.empty:
        frame = pd.concat([frame.assign(Rebuilt=False), rebuilt], ignore_index=True)
        frame = frame.sort_values("Date", ignore_index=True)
        logger.warning(
            "%s: Yahoo's daily feed had no bar for %s; rebuilt from hourly bars "
            "(volume estimated)",
            canonical, ", ".join(rebuilt_sessions(frame)),
        )
        missing = find_missing_sessions(frame["Date"], canonical)
    if missing:
        logger.warning(
            "%s: Yahoo returned no bar for %d trading session(s): %s, and they "
            "could not be rebuilt; indicators are computed across the gap",
            canonical, len(missing), ", ".join(missing),
        )
    return frame


def rebuilt_sessions(frame: pd.DataFrame) -> list[str]:
    """Dates (YYYY-MM-DD) of rows ``fill_missing_sessions`` rebuilt, oldest first."""
    if "Rebuilt" not in frame.columns:
        return []
    # Compare as text: the flag round-trips through the CSV cache.
    flagged = frame["Rebuilt"].astype(str).str.lower().eq("true")
    return sorted(pd.to_datetime(frame.loc[flagged, "Date"]).dt.strftime("%Y-%m-%d"))


def _needs_same_day_refresh(data_file, curr_date_dt, today_date) -> bool:
    """Whether a cached frame must be refetched to reflect the requested day.

    The cache file is keyed per day, so without this a run started before the
    day's bar was final keeps serving that snapshot to every later run (#1150).
    Two distinct staleness cases exist for a current-day request: the bar may be
    missing entirely, or present but still in progress — Yahoo publishes a
    partial daily candle during market hours, whose ``Close`` is not the closing
    price. Row inspection cannot tell a partial bar from a final one, so the TTL
    governs every current-day cache. Historical requests always reuse the cache,
    since those rows are immutable.
    """
    if curr_date_dt.date() < today_date.date():
        return False
    return time.time() - os.path.getmtime(data_file) > OHLCV_CACHE_TTL_SECONDS


def _write_cache(frame: pd.DataFrame, data_file: str) -> None:
    """Replace the cache file atomically; caching is best-effort.

    Parallel tool calls on a cold cache used to truncate and rewrite the same
    file at once, leaving the tail of the longer payload as a garbage last line.
    Each writer now fills its own temp file and swaps it in whole.
    """
    tmp = f"{data_file}.{os.getpid()}-{threading.get_ident()}.tmp"
    try:
        frame.to_csv(tmp, index=False, encoding="utf-8")
        os.replace(tmp, data_file)
    except OSError as exc:
        # e.g. Windows refuses to replace a file another reader or a racing
        # writer has open; the caller still uses the frame it downloaded.
        logger.warning("OHLCV cache %s not updated (%s); using the downloaded data", data_file, exc)
        with contextlib.suppress(OSError):
            os.remove(tmp)


def load_ohlcv(symbol: str, curr_date: str) -> pd.DataFrame:
    """Fetch OHLCV data with caching, filtered to prevent look-ahead bias.

    Downloads 5 years of data up to today and caches per symbol. On
    subsequent calls the cache is reused. Rows after curr_date are
    filtered out so backtests never see future prices.
    """
    # Resolve broker/forex symbols (XAUUSD+ -> GC=F) to Yahoo's convention,
    # then reject values that would escape the cache directory when
    # interpolated into the cache filename (e.g. ``../../tmp/x``).
    canonical = normalize_symbol(symbol)
    safe_symbol = safe_ticker_component(canonical)

    config = get_config()
    curr_date_dt = pd.to_datetime(curr_date)

    # Cache uses a fixed window (5y to today) so one file per symbol.
    today_date = pd.Timestamp.today()
    start_date = today_date - pd.DateOffset(years=5)
    start_str = start_date.strftime("%Y-%m-%d")
    # yfinance ``end`` is EXCLUSIVE; request tomorrow so today's row is included
    # when curr_date is the current day (#986). Look-ahead is still prevented by
    # the curr_date filter below.
    end_str = (today_date + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    os.makedirs(config["data_cache_dir"], exist_ok=True)
    data_file = os.path.join(
        config["data_cache_dir"],
        f"{safe_symbol}-YFin-data-{start_str}-{end_str}.csv",
    )

    # A cached file may be empty if a prior fetch failed (unknown symbol,
    # transient rate limit). Treat an empty/columnless cache as a miss and
    # re-fetch rather than serving the poisoned file forever.
    data = None
    if os.path.exists(data_file):
        cached = pd.read_csv(data_file, on_bad_lines="skip", encoding="utf-8")
        # Serve the cache only when it is usable and not a stale snapshot of the
        # day being requested (#1150); otherwise fall through and refetch.
        if (
            not cached.empty
            and "Close" in cached.columns
            and not _needs_same_day_refresh(data_file, curr_date_dt, today_date)
        ):
            data = cached

    if data is None:
        downloaded = yf_retry(lambda: yf.download(
            canonical,
            start=start_str,
            end=end_str,
            multi_level_index=False,
            progress=False,
            auto_adjust=True,
        ))
        downloaded = _ensure_date_column(downloaded.reset_index())
        # Only cache real data — never persist an empty frame.
        if downloaded.empty or "Close" not in downloaded.columns:
            raise NoMarketDataError(
                symbol, canonical, "Yahoo Finance returned no rows"
            )
        downloaded = fill_missing_sessions(downloaded, canonical)
        _write_cache(downloaded, data_file)
        data = downloaded

    data = _clean_dataframe(data)

    # Filter to curr_date to prevent look-ahead bias in backtesting
    data = data[data["Date"] <= curr_date_dt]

    # Reject a stale frame (latest row far older than curr_date) rather than
    # feeding year-old prices into indicators (#1021).
    _assert_ohlcv_not_stale(data, curr_date, symbol, canonical)

    return data


def filter_financials_by_date(data: pd.DataFrame, curr_date: str) -> pd.DataFrame:
    """Drop financial statement columns (fiscal period timestamps) after curr_date.

    yfinance financial statements use fiscal period end dates as columns.
    Columns after curr_date represent future data and are removed to
    prevent look-ahead bias.
    """
    if not curr_date or data.empty:
        return data
    cutoff = pd.Timestamp(curr_date)
    mask = pd.to_datetime(data.columns, errors="coerce") <= cutoff
    return data.loc[:, mask]


class StockstatsUtils:
    @staticmethod
    def get_stock_stats(
        symbol: Annotated[str, "ticker symbol for the company"],
        indicator: Annotated[
            str, "quantitative indicators based off of the stock data for the company"
        ],
        curr_date: Annotated[
            str, "curr date for retrieving stock price data, YYYY-mm-dd"
        ],
    ):
        data = load_ohlcv(symbol, curr_date)
        df = wrap(data)
        df["Date"] = df["Date"].dt.strftime("%Y-%m-%d")
        curr_date_str = pd.to_datetime(curr_date).strftime("%Y-%m-%d")

        df[indicator]  # trigger stockstats to calculate the indicator
        matching_rows = df[df["Date"].str.startswith(curr_date_str)]

        if not matching_rows.empty:
            indicator_value = matching_rows[indicator].values[0]
            return indicator_value
        else:
            return "N/A: Not a trading day (weekend or holiday)"
