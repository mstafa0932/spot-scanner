from __future__ import annotations

import html
import json
import logging
import os
import time

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

import requests

from market_data import (
    ParibuDataError,
    Ticker,
    fetch_candles,
    fetch_order_book,
    get_market_snapshot,
)

from indicator_engine import (
    calculate_indicators,
)


# ============================================================
# CONFIGURATION
# ============================================================

STATE_FILE = Path(
    os.getenv(
        "SCANNER_STATE_FILE",
        "scanner_state.json",
    )
)

MAX_SIGNALS_PER_RUN = 10

# We do not force weak trades.
MIN_SCORE = Decimal("70")

# Paribu execution rules.
MAX_ALLOWED_SPREAD_PCT = Decimal("0.80")

# Minimum gross distance from real Paribu Ask to TP1.
MIN_REQUIRED_TP1_PCT = Decimal("1.80")

# Preferred distance, used in scoring.
PREFERRED_TP1_PCT = Decimal("2.00")

# Minimum expected net TP1 return after estimated round trip fees.
MIN_NET_TP1_PCT = Decimal("1.00")

# Minimum local R:R.
MIN_REQUIRED_RR = Decimal("1.50")

# Current level-1 Paribu TL taker fee assumption.
# Replace through environment variable when your actual tier differs.
TAKER_FEE_PCT = Decimal(
    os.getenv(
        "PARIBU_TAKER_FEE_PCT",
        "0.28",
    )
)

# Cooldown.
COOLDOWN_SECONDS = int(
    os.getenv(
        "SIGNAL_COOLDOWN_SECONDS",
        str(4 * 60 * 60),
    )
)

# Technical scanning.
MAX_TECHNICAL_MARKETS = int(
    os.getenv(
        "MAX_TECHNICAL_MARKETS",
        "100",
    )
)

CANDLE_LIMIT = 250

ORDERBOOK_DEPTH = int(
    os.getenv(
        "ORDERBOOK_DEPTH",
        "50",
    )
)

# Do not call market APIs for obviously tiny markets.
MIN_QUOTE_VOLUME_TL = Decimal(
    os.getenv(
        "MIN_QUOTE_VOLUME_TL",
        "500000",
    )
)

# ============================================================
# LOGGER
# ============================================================

LOGGER = logging.getLogger(
    "spot_scanner"
)

if not LOGGER.handlers:

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(message)s"
        ),
    )


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class ExecutionResult:

    is_executable: bool

    reject_reason: Optional[str]

    entry_price: Decimal

    stop_loss: Decimal

    tp1: Decimal

    tp2: Decimal

    rr_ratio: Decimal

    spread_pct: Decimal

    tp1_pct: Decimal

    net_tp1_pct: Decimal

    depth_checked: bool = False

    depth_reason: Optional[str] = None


@dataclass
class ScanStats:

    total_markets: int = 0

    liquidity_pass: int = 0
    liquidity_fail: int = 0

    spread_pass: int = 0
    spread_fail: int = 0

    technical_attempted: int = 0

    candle_success: int = 0
    candle_fail: int = 0

    indicator_success: int = 0
    indicator_fail: int = 0

    execution_attempted: int = 0
    execution_pass: int = 0
    execution_fail: int = 0

    candidates_before_ranking: int = 0

    score_fail: int = 0

    tp1_fail: int = 0
    rr_fail: int = 0

    reasons: dict[str, int] = None

    def __post_init__(self):

        if self.reasons is None:

            self.reasons = {}


@dataclass(frozen=True)
class Opportunity:

    symbol: str

    score: int

    strength: str

    setup: str

    data_source: str

    current_price: Decimal

    paribu_bid: Decimal

    paribu_ask: Decimal

    spread_pct: Decimal

    entry_price: Decimal

    stop_loss: Decimal

    tp1: Decimal

    tp2: Decimal

    rr: Decimal

    tp1_pct: Decimal

    net_tp1_pct: Decimal

    rsi: Decimal

    atr_pct: Decimal

    volume_ratio: Decimal

    reason: str


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram_message(
    text: str,
) -> bool:

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:

        LOGGER.error(
            "Telegram credentials are missing."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15,
        )

        if response.status_code != 200:

            LOGGER.error(
                "Telegram HTTP %s: %s",
                response.status_code,
                response.text[:500],
            )

            return False

        return True

    except requests.RequestException as exc:

        LOGGER.error(
            "Telegram error: %s",
            exc,
        )

        return False


# ============================================================
# STATE
# ============================================================

def load_state() -> dict[str, Any]:

    if not STATE_FILE.exists():

        return {
            "sent_signals": {}
        }

    try:

        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as file:

            data = json.load(file)

        if not isinstance(
            data,
            dict,
        ):

            return {
                "sent_signals": {}
            }

        data.setdefault(
            "sent_signals",
            {},
        )

        return data

    except Exception as exc:

        LOGGER.warning(
            "State load failed: %s",
            exc,
        )

        return {
            "sent_signals": {}
        }


def save_state(
    state: dict[str, Any],
) -> None:

    temporary = (
        STATE_FILE.with_suffix(
            ".tmp"
        )
    )

    try:

        with temporary.open(
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                state,
                file,
                indent=2,
                ensure_ascii=False,
            )

        temporary.replace(
            STATE_FILE
        )

    except Exception as exc:

        LOGGER.error(
            "State save failed: %s",
            exc,
        )


def cooldown_key(
    symbol: str,
) -> str:

    return symbol


def cooldown_allowed(
    symbol: str,
    state: dict[str, Any],
) -> bool:

    sent = state.get(
        "sent_signals",
        {},
    )

    last_timestamp = sent.get(
        cooldown_key(symbol)
    )

    if last_timestamp is None:

        return True

    try:

        last_timestamp = int(
            last_timestamp
        )

    except (
        ValueError,
        TypeError,
    ):

        return True

    return (
        time.time()
        - last_timestamp
        >= COOLDOWN_SECONDS
    )


# ============================================================
# PRICE PRECISION
# ============================================================

def price_step(
    price: Decimal,
) -> Decimal:

    if price >= Decimal("1000"):
        return Decimal("0.01")

    if price >= Decimal("100"):
        return Decimal("0.01")

    if price >= Decimal("1"):
        return Decimal("0.0001")

    if price >= Decimal("0.01"):
        return Decimal("0.000001")

    if price >= Decimal("0.0001"):
        return Decimal("0.00000001")

    return Decimal("0.0000000001")


def format_price(
    price: Decimal,
) -> str:

    step = price_step(
        price
    )

    return format(
        price.quantize(step),
        "f",
    )


# ============================================================
# INDICATOR COMPATIBILITY
# ============================================================

def _indicator_value(
    indicators: Any,
    name: str,
    default: Any = None,
) -> Any:

    # Dict
    if isinstance(
        indicators,
        dict,
    ):

        return indicators.get(
            name,
            default,
        )

    # Attribute
    if hasattr(
        indicators,
        name,
    ):

        return getattr(
            indicators,
            name,
        )

    return default


def normalize_indicator_output(
    result: Any,
) -> Optional[dict[str, Any]]:

    if result is None:

        return None

    # Existing dictionary-style indicator engine.
    if isinstance(
        result,
        dict,
    ):

        return result

    # Existing dataclass-style indicator engine.
    if hasattr(
        result,
        "current_close",
    ):

        return {
            "close": getattr(
                result,
                "current_close",
            ),
            "rsi": getattr(
                result,
                "rsi14",
                Decimal("0"),
            ),
            "atr": getattr(
                result,
                "atr14",
                Decimal("0"),
            ),
            "volume_ratio": getattr(
                result,
                "volume_ratio",
                Decimal("0"),
            ),
            "reason": (
                "technical setup"
            ),
            "is_valid_setup": True,
            "data_source": "Binance",
        }

    # Some previous versions returned a DataFrame.
    # Convert the latest CLOSED row into the fields scanner needs.
    try:

        import pandas as pd

        if isinstance(
            result,
            pd.DataFrame,
        ):

            df = result

            if len(df) < 2:

                return None

            row = df.iloc[-2]

            def val(
                *names,
                default=None,
            ):

                for name in names:

                    if name in row.index:

                        return row[name]

                return default

            close = Decimal(
                str(
                    val(
                        "close",
                        "Close",
                        default=0,
                    )
                )
            )

            if close <= 0:

                return None

            rsi = Decimal(
                str(
                    val(
                        "RSI14",
                        "RSI_14",
                        default=50,
                    )
                )
            )

            atr = Decimal(
                str(
                    val(
                        "ATR14",
                        "ATR_14",
                        default=0,
                    )
                )
            )

            volume = Decimal(
                str(
                    val(
                        "volume",
                        default=0,
                    )
                )
            )

            vol_sma = Decimal(
                str(
                    val(
                        "VOL_SMA20",
                        "VOL_SMA_20",
                        default=0,
                    )
                )
            )

            volume_ratio = (
                volume
                / vol_sma
                if vol_sma > 0
                else Decimal("0")
            )

            ema21 = Decimal(
                str(
                    val(
                        "EMA21",
                        "EMA_21",
                        default=close,
                    )
                )
            )

            ema50 = Decimal(
                str(
                    val(
                        "EMA50",
                        "EMA_50",
                        default=close,
                    )
                )
            )

            ema200 = Decimal(
                str(
                    val(
                        "EMA200",
                        "EMA_200",
                        default=close,
                    )
                )
            )

            macd = Decimal(
                str(
                    val(
                        "MACD",
                        "MACD_line",
                        default=0,
                    )
                )
            )

            macd_signal = Decimal(
                str(
                    val(
                        "MACD_SIGNAL",
                        "MACD_signal",
                        default=0,
                    )
                )
            )

            trend = (
                close > ema50
                and ema50 > ema200
            )

            pullback = (
                bool(
                    val(
                        "is_pullback",
                        default=False,
                    )
                )
                or (
                    close > ema21
                    and (
                        df["low"]
                        .iloc[
                            -5:-1
                        ]
                        .min()
                        <= float(ema21)
                    )
                )
            )

            bullish_candle = (
                close
                > Decimal(
                    str(
                        val(
                            "open",
                            default=close,
                        )
                    )
                )
            )

            return {
                "close": close,
                "rsi": rsi,
                "atr": atr,
                "volume_ratio": volume_ratio,
                "is_valid_setup": True,
                "is_uptrend": trend,
                "is_above_ema21": (
                    close > ema21
                ),
                "pullback": pullback,
                "breakout": False,
                "bullish_candle": bullish_candle,
                "macd_bullish": (
                    macd > macd_signal
                ),
                "reason": "technical setup",
                "data_source": (
                    df.attrs.get(
                        "source",
                        "Binance",
                    )
                ),
            }

    except Exception as exc:

        LOGGER.debug(
            "Could not normalize indicator DataFrame: %s",
            exc,
        )

    return None


# ============================================================
# TECHNICAL SCORING
# ============================================================

def calculate_score(
    indicators: dict[str, Any],
    ticker: Ticker,
) -> tuple[
    int,
    str,
    list[str],
]:

    score = 0

    reasons = []

    rsi = Decimal(
        str(
            indicators.get(
                "rsi",
                50,
            )
        )
    )

    volume_ratio = Decimal(
        str(
            indicators.get(
                "volume_ratio",
                0,
            )
        )
    )

    is_uptrend = bool(
        indicators.get(
            "is_uptrend",
            False,
        )
    )

    above_ema21 = bool(
        indicators.get(
            "is_above_ema21",
            False,
        )
    )

    pullback = bool(
        indicators.get(
            "pullback",
            False,
        )
    )

    breakout = bool(
        indicators.get(
            "breakout",
            False,
        )
    )

    bullish_candle = bool(
        indicators.get(
            "bullish_candle",
            False,
        )
    )

    macd_bullish = bool(
        indicators.get(
            "macd_bullish",
            False,
        )
    )

    # --------------------------------------------------------
    # Trend — 25
    # --------------------------------------------------------

    if is_uptrend:

        score += 15

        reasons.append(
            "اتجاه صاعد"
        )

    if above_ema21:

        score += 10

    # --------------------------------------------------------
    # RSI — 15
    # --------------------------------------------------------

    if (
        Decimal("52")
        <= rsi
        <= Decimal("66")
    ):

        score += 15

        reasons.append(
            "RSI صحي"
        )

    elif (
        Decimal("48")
        <= rsi
        < Decimal("52")
    ):

        score += 9

    elif (
        Decimal("66")
        < rsi
        <= Decimal("70")
    ):

        score += 8

    # --------------------------------------------------------
    # MACD — 10
    # --------------------------------------------------------

    if macd_bullish:

        score += 10

        reasons.append(
            "MACD داعم"
        )

    # --------------------------------------------------------
    # Volume — 15
    # --------------------------------------------------------

    if volume_ratio >= Decimal("2"):

        score += 15

        reasons.append(
            "حجم قوي"
        )

    elif volume_ratio >= Decimal("1.5"):

        score += 12

    elif volume_ratio >= Decimal("1.15"):

        score += 8

    elif volume_ratio >= Decimal("1"):

        score += 5

    # --------------------------------------------------------
    # Setup — 15
    # --------------------------------------------------------

    if pullback:

        score += 10

        reasons.append(
            "Pullback"
        )

    if breakout:

        score += 5

        reasons.append(
            "Breakout"
        )

    elif bullish_candle:

        score += 3

        reasons.append(
            "شمعة إيجابية"
        )

    # --------------------------------------------------------
    # Local execution — 20
    # --------------------------------------------------------

    spread = (
        ticker.spread_percent
    )

    if spread is not None:

        if spread <= Decimal("0.30"):

            score += 10

            reasons.append(
                "سبريد محلي ممتاز"
            )

        elif spread <= Decimal("0.50"):

            score += 7

        elif spread <= Decimal("0.80"):

            score += 4

    if (
        ticker.quote_volume
        is not None
        and ticker.quote_volume
        >= Decimal("3000000")
    ):

        score += 10

    elif (
        ticker.quote_volume
        is not None
        and ticker.quote_volume
        >= Decimal("1500000")
    ):

        score += 7

    elif (
        ticker.quote_volume
        is not None
        and ticker.quote_volume
        >= Decimal("750000")
    ):

        score += 4

    score = min(
        max(score, 0),
        100,
    )

    if score >= 90:

        strength = (
            "🔥 EXCEPTIONAL"
        )

    elif score >= 85:

        strength = (
            "🟢 STRONG"
        )

    elif score >= 78:

        strength = (
            "🟡 GOOD"
        )

    else:

        strength = (
            "🔵 WATCH"
        )

    return (
        score,
        strength,
        reasons,
    )


# ============================================================
# TRADE LEVELS
# ============================================================

def build_initial_levels(
    indicators: dict[str, Any],
    ticker: Ticker,
) -> tuple[
    Decimal,
    Decimal,
    Decimal,
    Decimal,
]:

    entry = (
        ticker.ask
        if ticker.ask is not None
        and ticker.ask > 0
        else ticker.last
    )

    if entry <= 0:

        raise ValueError(
            "Invalid Paribu entry."
        )

    atr = Decimal(
        str(
            indicators.get(
                "atr",
                0,
            )
        )
    )

    if atr <= 0:

        raise ValueError(
            "Invalid ATR."
        )

    # ATR from Binance is a percentage/volatility reference.
    # Use it as a local percentage rather than copying its
    # absolute price into Paribu.
    atr_pct = (
        atr
        / Decimal(
            str(
                indicators.get(
                    "close",
                    entry,
                )
            )
        )
    )

    if atr_pct <= 0:

        raise ValueError(
            "Invalid ATR percentage."
        )

    risk_pct = max(
        Decimal("0.012"),
        min(
            atr_pct
            * Decimal("1.35"),
            Decimal("0.06"),
        ),
    )

    risk = (
        entry
        * risk_pct
    )

    stop = (
        entry
        - risk
    )

    if stop <= 0:

        raise ValueError(
            "Stop loss would be <= 0."
        )

    tp1 = (
        entry
        + (
            risk
            * Decimal("1.60")
        )
    )

    tp2 = (
        entry
        + (
            risk
            * Decimal("2.40")
        )
    )

    return (
        entry,
        stop,
        tp1,
        tp2,
    )


# ============================================================
# EXECUTION FILTER
# ============================================================

def evaluate_execution(
    ticker: Ticker,
    indicators: dict[str, Any],
    stop_loss: Decimal,
    tp1: Decimal,
    tp2: Decimal,
) -> ExecutionResult:

    if ticker.bid is None:

        return ExecutionResult(
            False,
            "Paribu Bid غير متوفر",
            ticker.ask or ticker.last,
            tp1,
            stop_loss,
            Decimal("0"),
            Decimal("0"),
            Decimal("99"),
            Decimal("0"),
            Decimal("0"),
        )

    if ticker.ask is None:

        return ExecutionResult(
            False,
            "Paribu Ask غير متوفر",
            ticker.last,
            tp1,
            stop_loss,
            Decimal("0"),
            Decimal("0"),
            Decimal("99"),
            Decimal("0"),
            Decimal("0"),
        )

    entry = ticker.ask

    spread = ticker.spread_percent

    if spread is None:

        return ExecutionResult(
            False,
            "السبريد المحلي غير متوفر",
            entry,
            tp1,
            stop_loss,
            Decimal("0"),
            Decimal("0"),
            Decimal("99"),
            Decimal("0"),
            Decimal("0"),
        )

    if spread > MAX_ALLOWED_SPREAD_PCT:

        return ExecutionResult(
            False,
            (
                f"رفض: Paribu Spread = "
                f"{spread:.2f}%"
            ),
            entry,
            tp1,
            stop_loss,
            Decimal("0"),
            Decimal("0"),
            spread,
            Decimal("0"),
            Decimal("0"),
        )

    # --------------------------------------------------------
    # Rebuild SL/TP relative to REAL Paribu Ask.
    # Never use Binance absolute price levels here.
    # --------------------------------------------------------

    close = Decimal(
        str(
            indicators.get(
                "close",
                entry,
            )
        )
    )

    if close <= 0:

        return ExecutionResult(
            False,
            "سعر الشمعة المرجعية غير صالح",
            entry,
            tp1,
            stop_loss,
            Decimal("0"),
            Decimal("0"),
            spread,
            Decimal("0"),
            Decimal("0"),
        )

    stop_distance = (
        abs(
            close
            - stop_loss
        )
    )

    tp1_distance = (
        abs(
            tp1
            - close
        )
    )

    tp2_distance = (
        abs(
            tp2
            - close
        )
    )

    # Convert percentage movement implied by Binance
    # into a Paribu-local price distance.
    stop_pct = (
        stop_distance
        / close
    )

    tp1_pct_reference = (
        tp1_distance
        / close
    )

    tp2_pct_reference = (
        tp2_distance
        / close
    )

    local_stop = (
        entry
        - (
            entry
            * stop_pct
        )
    )

    local_tp1 = (
        entry
        + (
            entry
            * tp1_pct_reference
        )
    )

    local_tp2 = (
        entry
        + (
            entry
            * tp2_pct_reference
        )
    )

    if (
        local_stop <= 0
        or local_tp1 <= entry
        or local_tp2 <= entry
    ):

        return ExecutionResult(
            False,
            "مستويات Paribu المحلية غير منطقية",
            entry,
            local_tp1,
            local_stop,
            Decimal("0"),
            Decimal("0"),
            spread,
            Decimal("0"),
            Decimal("0"),
        )

    # TP1 gross percentage.
    gross_tp1_pct = (
        (local_tp1 - entry)
        / entry
        * Decimal("100")
    )

    if (
        gross_tp1_pct
        < MIN_REQUIRED_TP1_PCT
    ):

        return ExecutionResult(
            False,
            (
                f"TP1 بعيد فقط "
                f"{gross_tp1_pct:.2f}% "
                f"عن Paribu Ask"
            ),
            entry,
            local_tp1,
            local_stop,
            Decimal("0"),
            Decimal("0"),
            spread,
            gross_tp1_pct,
            Decimal("0"),
        )

    # --------------------------------------------------------
    # Net return after round-trip taker fees.
    # We use a conservative half-spread execution allowance.
    # --------------------------------------------------------

    round_trip_fee = (
        TAKER_FEE_PCT
        * Decimal("2")
    )

    spread_cost = (
        spread
        / Decimal("2")
    )

    total_cost = (
        round_trip_fee
        + spread_cost
    )

    net_tp1_pct = (
        gross_tp1_pct
        - total_cost
    )

    if (
        net_tp1_pct
        < MIN_NET_TP1_PCT
    ):

        return ExecutionResult(
            False,
            (
                f"الربح الصافي المتوقع "
                f"{net_tp1_pct:.2f}% "
                f"غير كافٍ بعد الرسوم"
            ),
            entry,
            local_tp1,
            local_stop,
            gross_tp1_pct,
            total_cost,
            spread,
            gross_tp1_pct,
            net_tp1_pct,
        )

    risk = (
        entry
        - local_stop
    )

    reward = (
        local_tp1
        - entry
    )

    if risk <= 0 or reward <= 0:

        return ExecutionResult(
            False,
            "R:R غير صالح",
            entry,
            local_tp1,
            local_stop,
            gross_tp1_pct,
            total_cost,
            spread,
            gross_tp1_pct,
            net_tp1_pct,
        )

    rr = (
        reward
        / risk
    )

    if rr < MIN_REQUIRED_RR:

        return ExecutionResult(
            False,
            (
                f"R:R = {rr:.2f}"
            ),
            entry,
            local_tp1,
            local_stop,
            gross_tp1_pct,
            total_cost,
            spread,
            gross_tp1_pct,
            net_tp1_pct,
        )

    # --------------------------------------------------------
    # Optional order-book check.
    #
    # We do NOT assume a fake endpoint. If it is unavailable,
    # the signal may still pass Level-1 execution checks.
    # The Telegram message explicitly tells us depth was not checked.
    # --------------------------------------------------------

    depth_checked = False
    depth_reason = None

    try:

        order_book = fetch_order_book(
            ticker.symbol,
            depth=ORDERBOOK_DEPTH,
        )

        depth_checked = True

        asks = order_book.get(
            "asks",
            [],
        )

        bids = order_book.get(
            "bids",
            [],
        )

        if not asks or not bids:

            return ExecutionResult(
                False,
                "دفتر الأوامر فارغ",
                entry,
                local_tp1,
                local_stop,
                gross_tp1_pct,
                total_cost,
                spread,
                gross_tp1_pct,
                net_tp1_pct,
                True,
                "Empty order book",
            )

        # Find the amount of sell-side liquidity between entry and TP1.
        sell_value_to_tp1 = Decimal("0")

        largest_wall_value = Decimal("0")

        for level in asks:

            if (
                not isinstance(
                    level,
                    (list, tuple),
                )
                or len(level) < 2
            ):

                continue

            try:

                level_price = Decimal(
                    str(level[0])
                )

                level_qty = Decimal(
                    str(level[1])
                )

            except Exception:

                continue

            if (
                level_price <= 0
                or level_qty <= 0
            ):

                continue

            if (
                entry
                <= level_price
                <= local_tp1
            ):

                value = (
                    level_price
                    * level_qty
                )

                sell_value_to_tp1 += value

                if value > largest_wall_value:

                    largest_wall_value = value

        # We deliberately use a conservative heuristic:
        # an unusually large wall relative to the estimated position
        # is grounds for rejection.
        # The actual trade size is configurable.
        trade_size = Decimal(
            os.getenv(
                "SCANNER_TRADE_SIZE_TL",
                "5000",
            )
        )

        if (
            trade_size > 0
            and largest_wall_value
            >= trade_size * Decimal("5")
        ):

            return ExecutionResult(
                False,
                "جدار بيع كبير قبل TP1",
                entry,
                local_tp1,
                local_stop,
                gross_tp1_pct,
                total_cost,
                spread,
                gross_tp1_pct,
                net_tp1_pct,
                True,
                (
                    "Large sell wall before TP1"
                ),
            )

        depth_reason = (
            f"Depth OK; sell-side path "
            f"value={sell_value_to_tp1:.2f}"
        )

    except Exception as exc:

        # Do not silently claim order-book validation.
        depth_checked = False

        depth_reason = (
            f"Depth unavailable: {exc}"
        )

    return ExecutionResult(
        True,
        None,
        entry,
        local_tp1,
        local_stop,
        gross_tp1_pct,
        total_cost,
        spread,
        gross_tp1_pct,
        net_tp1_pct,
        depth_checked,
        depth_reason,
    )


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal_message(
    opportunity: Opportunity,
    rank: int,
) -> str:

    depth_text = (
        "✅ مفحوص"
        if opportunity.data_source
        else "⚠️ غير مفحوص"
    )

    return (
        f"🎯 <b>SPOT ENTRY #{rank}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{html.escape(opportunity.symbol)}</b>\n"
        f"💪 <b>القوة:</b> "
        f"{opportunity.strength}\n"
        f"📊 <b>Score:</b> "
        f"{opportunity.score}/100\n"
        f"🧩 <b>Setup:</b> "
        f"{opportunity.setup}\n\n"

        f"💵 <b>Paribu Ask:</b> "
        f"<code>{format_price(opportunity.paribu_ask)}</code>\n"
        f"💵 <b>Entry:</b> "
        f"<code>{format_price(opportunity.entry_price)}</code>\n"
        f"🛑 <b>SL:</b> "
        f"<code>{format_price(opportunity.stop_loss)}</code>\n"
        f"🎯 <b>TP1:</b> "
        f"<code>{format_price(opportunity.tp1)}</code>\n"
        f"🚀 <b>TP2:</b> "
        f"<code>{format_price(opportunity.tp2)}</code>\n\n"

        f"📐 <b>R:R:</b> "
        f"1:{opportunity.rr}\n"
        f"📈 <b>TP1:</b> "
        f"+{opportunity.tp1_pct}%\n"
        f"💰 <b>صافي TP1:</b> "
        f"+{opportunity.net_tp1_pct}%\n"
        f"📏 <b>Spread Paribu:</b> "
        f"{opportunity.spread_pct}%\n\n"

        f"📊 <b>RSI:</b> "
        f"{opportunity.rsi}\n"
        f"📊 <b>ATR:</b> "
        f"{opportunity.atr_pct}%\n"
        f"💧 <b>Volume:</b> "
        f"{opportunity.volume_ratio}x\n"
        f"📡 <b>مصدر الشموع:</b> "
        f"{html.escape(opportunity.data_source)}\n\n"

        f"🧠 <b>السبب:</b> "
        f"{html.escape(opportunity.reason)}\n\n"

        "⚠️ <b>Spot فقط — التنفيذ يدوي.</b>"
    )


# ============================================================
# NO SIGNAL REPORT
# ============================================================

def format_no_signal_report(
    stats: ScanStats,
) -> str:

    return (
        "🔍 <b>Paribu — التقرير الدوري</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 الأزواج: "
        f"{stats.total_markets}\n"
        f"💧 اجتازت السيولة: "
        f"{stats.liquidity_pass}\n"
        f"❌ رفض السيولة: "
        f"{stats.liquidity_fail}\n"
        f"📏 اجتازت السبريد: "
        f"{stats.spread_pass}\n"
        f"❌ رفض السبريد: "
        f"{stats.spread_fail}\n"
        f"🕯️ نجاح الشموع: "
        f"{stats.candle_success}\n"
        f"❌ أخطاء الشموع: "
        f"{stats.candle_fail}\n"
        f"📐 نجاح المؤشرات: "
        f"{stats.indicator_success}\n"
        f"❌ فشل المؤشرات: "
        f"{stats.indicator_fail}\n"
        f"⚙️ محاولات التنفيذ: "
        f"{stats.execution_attempted}\n"
        f"✅ تنفيذ مقبول: "
        f"{stats.execution_pass}\n"
        f"❌ تنفيذ مرفوض: "
        f"{stats.execution_fail}\n"
        f"⭐ فشل Score: "
        f"{stats.score_fail}\n"
        f"🎯 فشل TP1: "
        f"{stats.tp1_fail}\n"
        f"📐 فشل R:R: "
        f"{stats.rr_fail}\n\n"
        "💡 <b>لا توجد صفقة إجبارية.</b>\n"
        "البوت يفضّل عدم إرسال الصفقة عندما "
        "لا تكون اقتصادية أو قابلة للتنفيذ."
    )


# ============================================================
# SCANNER
# ============================================================

def run_scanner() -> None:

    LOGGER.info(
        "Starting Paribu Spot Scanner..."
    )

    try:

        snapshot = (
            get_market_snapshot()
        )

    except ParibuDataError as exc:

        LOGGER.error(
            "Paribu market data error: %s",
            exc,
        )

        send_telegram_message(
            "🚨 <b>SCANNER ERROR</b>\n\n"
            f"{html.escape(str(exc))}"
        )

        return

    stats = ScanStats()

    stats.total_markets = len(
        snapshot
    )

    state = load_state()

    candidates: list[
        Opportunity
    ] = []

    # High-liquidity pairs first.
    tickers = sorted(
        snapshot.values(),
        key=lambda x: (
            x.quote_volume
            if x.quote_volume is not None
            else Decimal("0")
        ),
        reverse=True,
    )

    for ticker in tickers:

        # ----------------------------------------------------
        # Liquidity
        # ----------------------------------------------------

        if (
            ticker.quote_volume is None
            or ticker.quote_volume
            < MIN_QUOTE_VOLUME_TL
        ):

            stats.liquidity_fail += 1

            continue

        stats.liquidity_pass += 1

        # ----------------------------------------------------
        # Level-1 local spread
        # ----------------------------------------------------

        if ticker.spread_percent is None:

            stats.spread_fail += 1

            continue

        if (
            ticker.spread_percent
            > MAX_ALLOWED_SPREAD_PCT
        ):

            stats.spread_fail += 1

            continue

        stats.spread_pass += 1

        # ----------------------------------------------------
        # Technical market limit
        # ----------------------------------------------------

        if (
            stats.technical_attempted
            >= MAX_TECHNICAL_MARKETS
        ):

            break

        stats.technical_attempted += 1

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        try:

            df = fetch_candles(
                ticker.symbol,
                resolution="15m",
                limit=CANDLE_LIMIT,
            )

            stats.candle_success += 1

        except Exception as exc:

            stats.candle_fail += 1

            LOGGER.debug(
                "Candle failure %s: %s",
                ticker.symbol,
                exc,
            )

            continue

        # ----------------------------------------------------
        # Indicators
        # ----------------------------------------------------

        try:

            raw_indicators = (
                calculate_indicators(
                    df
                )
            )

            indicators = (
                normalize_indicator_output(
                    raw_indicators
                )
            )

        except Exception as exc:

            stats.indicator_fail += 1

            LOGGER.debug(
                "Indicator error %s: %s",
                ticker.symbol,
                exc,
            )

            continue

        if indicators is None:

            stats.indicator_fail += 1

            continue

        stats.indicator_success += 1

        # ----------------------------------------------------
        # Basic technical gate
        # ----------------------------------------------------

        rsi = Decimal(
            str(
                indicators.get(
                    "rsi",
                    50,
                )
            )
        )

        if rsi >= Decimal("75"):

            stats.reasons[
                "RSI"
            ] = (
                stats.reasons.get(
                    "RSI",
                    0,
                )
                + 1
            )

            continue

        # Avoid extreme negative trend.
        is_uptrend = bool(
            indicators.get(
                "is_uptrend",
                False,
            )
        )

        above_ema21 = bool(
            indicators.get(
                "is_above_ema21",
                False,
            )
        )

        pullback = bool(
            indicators.get(
                "pullback",
                False,
            )
        )

        breakout = bool(
            indicators.get(
                "breakout",
                False,
            )
        )

        bullish_candle = bool(
            indicators.get(
                "bullish_candle",
                False,
            )
        )

        # Balanced setup gate:
        # We do not require every indicator simultaneously.
        if not (
            (
                is_uptrend
                and above_ema21
            )
            or pullback
            or breakout
            or (
                bullish_candle
                and above_ema21
            )
        ):

            stats.reasons[
                "SETUP"
            ] = (
                stats.reasons.get(
                    "SETUP",
                    0,
                )
                + 1
            )

            continue

        # ----------------------------------------------------
        # Score
        # ----------------------------------------------------

        score, strength, reasons = (
            calculate_score(
                indicators,
                ticker,
            )
        )

        if score < int(
            MIN_SCORE
        ):

            stats.score_fail += 1

            continue

        # ----------------------------------------------------
        # Build levels
        # ----------------------------------------------------

        try:

            (
                entry,
                stop,
                tp1,
                tp2,
            ) = build_initial_levels(
                indicators,
                ticker,
            )

        except Exception as exc:

            stats.rr_fail += 1

            LOGGER.debug(
                "Level error %s: %s",
                ticker.symbol,
                exc,
            )

            continue

        # ----------------------------------------------------
        # Execution filter
        # ----------------------------------------------------

        stats.execution_attempted += 1

        execution = evaluate_execution(
            ticker=ticker,
            indicators=indicators,
            stop_loss=stop,
            tp1=tp1,
            tp2=tp2,
        )

        if not execution.is_executable:

            stats.execution_fail += 1

            if (
                "TP1" in (
                    execution.reject_reason
                    or ""
                )
            ):

                stats.tp1_fail += 1

            if (
                "R:R" in (
                    execution.reject_reason
                    or ""
                )
            ):

                stats.rr_fail += 1

            LOGGER.info(
                "Rejected %s: %s",
                ticker.symbol,
                execution.reject_reason,
            )

            continue

        stats.execution_pass += 1
        stats.candidates_before_ranking += 1

        # ----------------------------------------------------
        # Cooldown
        # ----------------------------------------------------

        if not cooldown_allowed(
            ticker.symbol,
            state,
        ):

            LOGGER.info(
                "Cooldown: %s",
                ticker.symbol,
            )

            continue

        # ----------------------------------------------------
        # ATR %
        # ----------------------------------------------------

        atr = Decimal(
            str(
                indicators.get(
                    "atr",
                    0,
                )
            )
        )

        reference_close = Decimal(
            str(
                indicators.get(
                    "close",
                    ticker.last,
                )
            )
        )

        atr_pct = (
            atr
            / reference_close
            * Decimal("100")
            if (
                atr > 0
                and reference_close > 0
            )
            else Decimal("0")
        )

        volume_ratio = Decimal(
            str(
                indicators.get(
                    "volume_ratio",
                    0,
                )
            )
        )

        setup = (
            "BREAKOUT"
            if breakout
            else "PULLBACK"
            if pullback
            else "MOMENTUM"
        )

        data_source = str(
            indicators.get(
                "data_source",
                df.attrs.get(
                    "source",
                    "Binance",
                ),
            )
        )

        current_price = (
            ticker.last
        )

        reason_text = (
            " | ".join(
                reasons[:6]
            )
            if reasons
            else "technical confirmation"
        )

        opportunity = Opportunity(
            symbol=ticker.symbol,
            score=int(score),
            strength=strength,
            setup=setup,
            data_source=data_source,
            current_price=current_price,
            paribu_bid=ticker.bid,
            paribu_ask=ticker.ask,
            spread_pct=execution.spread_pct.quantize(
                Decimal("0.01")
            ),
            entry_price=execution.entry_price,
            stop_loss=execution.stop_loss,
            tp1=execution.tp1,
            tp2=execution.tp2,
            rr=execution.rr_ratio.quantize(
                Decimal("0.01")
            ),
            tp1_pct=execution.tp1_pct.quantize(
                Decimal("0.01")
            ),
            net_tp1_pct=execution.net_tp1_pct.quantize(
                Decimal("0.01")
            ),
            rsi=rsi.quantize(
                Decimal("0.1")
            ),
            atr_pct=atr_pct.quantize(
                Decimal("0.01")
            ),
            volume_ratio=volume_ratio.quantize(
                Decimal("0.01")
            ),
            reason=(
                reason_text
                + (
                    f" | {execution.depth_reason}"
                    if execution.depth_reason
                    else ""
                )
            ),
        )

        candidates.append(
            opportunity
        )

    # --------------------------------------------------------
    # Ranking
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x.score,
            x.rr,
            x.net_tp1_pct,
            x.volume_ratio,
        ),
        reverse=True,
    )

    final_candidates = candidates[
        :MAX_SIGNALS_PER_RUN
    ]

    if not final_candidates:

        report = (
            format_no_signal_report(
                stats
            )
        )

        LOGGER.info(
            "No executable opportunities."
        )

        send_telegram_message(
            report
        )

        # Save state even when no signal.
        save_state(
            state
        )

        return

    # --------------------------------------------------------
    # Send alerts
    # --------------------------------------------------------

    header = (
        "🔥 <b>Paribu — فرص Spot</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"تم العثور على "
        f"<b>{len(final_candidates)}</b> "
        "فرصة قابلة للتنفيذ.\n"
        "مرتبة من الأقوى إلى الأضعف."
    )

    send_telegram_message(
        header
    )

    for rank, opportunity in enumerate(
        final_candidates,
        start=1,
    ):

        message = (
            format_signal_message(
                opportunity,
                rank,
            )
        )

        if send_telegram_message(
            message
        ):

            state[
                "sent_signals"
            ][
                opportunity.symbol
            ] = int(
                time.time()
            )

            save_state(
                state
            )

            LOGGER.info(
                "Sent %s",
                opportunity.symbol,
            )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        run_scanner()

    except Exception as exc:

        LOGGER.exception(
            "FATAL SCANNER ERROR"
        )

        error_text = (
            "🚨 <b>SPOT SCANNER ERROR</b>\n\n"
            f"<code>{html.escape(str(exc))}</code>"
        )

        send_telegram_message(
            error_text
        )

        raise
