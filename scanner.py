from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from typing import Dict, List, Tuple
import json
import time

from market_data import (
    get_market_snapshot,
    fetch_candles,
    Ticker,
)

from indicator_engine import (
    analyze_symbol,
    IndicatorResult,
)


TOP_N = 10

# Do not force a trade.
MIN_ACTION_SCORE = 70

# Liquidity
MIN_QUOTE_VOLUME_TL = Decimal("500000")

# Spread
MAX_SPREAD_PERCENT = Decimal("1.50")

# RSI
RSI_HARD_MAX = Decimal("75")

# FOMO protection
MAX_3_CANDLE_RETURN = Decimal("4.0")
MAX_12_CANDLE_RETURN = Decimal("10.0")

# Volatility
MIN_ATR_PERCENT = Decimal("0.20")
MAX_ATR_PERCENT = Decimal("10.0")

# Risk
MIN_RR = Decimal("1.50")

ATR_STOP_MULTIPLIER = Decimal("1.35")

# Limit technical requests.
# Tickers still cover the entire market.
MAX_TECHNICAL_MARKETS = 100

# Dedup
COOLDOWN_SECONDS = 4 * 60 * 60

STATE_FILE = Path("scanner_state.json")


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

    rsi_fail: int = 0
    trend_fail: int = 0
    fomo_fail: int = 0
    volatility_fail: int = 0
    setup_fail: int = 0
    rr_fail: int = 0
    score_fail: int = 0

    candidates_before_rank: int = 0

    strongest_rejections: List[str] = field(
        default_factory=list
    )


@dataclass(frozen=True)
class Opportunity:

    symbol: str

    score: int
    strength: str

    setup: str

    current_price: Decimal

    entry_price: Decimal
    stop_loss: Decimal
    tp_1: Decimal
    tp_2: Decimal

    rr: Decimal

    rsi: Decimal
    atr_percent: Decimal
    volume_ratio: Decimal

    reason: str


def load_state() -> Dict[str, float]:

    if not STATE_FILE.exists():
        return {}

    try:

        with STATE_FILE.open(
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return {}

        return {
            str(k): float(v)
            for k, v in data.items()
        }

    except Exception:

        return {}


def save_state(
    state: Dict[str, float],
) -> None:

    with STATE_FILE.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            state,
            f,
            indent=2,
            sort_keys=True,
        )


def precision_for_price(
    price: Decimal,
) -> Decimal:

    if price >= 1000:
        return Decimal("0.01")

    if price >= 100:
        return Decimal("0.01")

    if price >= 1:
        return Decimal("0.0001")

    if price >= Decimal("0.01"):
        return Decimal("0.000001")

    if price >= Decimal("0.0001"):
        return Decimal("0.00000001")

    return Decimal("0.0000000001")


def quantize_price(
    value: Decimal,
    step: Decimal,
) -> Decimal:

    return value.quantize(
        step,
        rounding=ROUND_DOWN,
    )


def strength_from_score(
    score: int,
) -> str:

    if score >= 90:
        return "🔥 VERY STRONG"

    if score >= 85:
        return "🟢 STRONG"

    if score >= 78:
        return "🟡 GOOD"

    return "🔵 WATCH"


def score_candidate(
    ind: IndicatorResult,
    ticker: Ticker,
) -> Tuple[int, List[str]]:

    score = 0

    reasons: List[str] = []

    # -------------------------------------------------
    # 1. TREND / 20
    # -------------------------------------------------

    if ind.is_uptrend:

        score += 12

        reasons.append(
            "trend aligned"
        )

    if ind.is_above_ema9:

        score += 4

    if ind.is_above_ema21:

        score += 4

    # -------------------------------------------------
    # 2. RSI / 10
    # -------------------------------------------------

    if (
        Decimal("52")
        <= ind.rsi14
        <= Decimal("66")
    ):

        score += 10

        reasons.append(
            "healthy RSI"
        )

    elif (
        Decimal("48")
        <= ind.rsi14
        < Decimal("52")
    ):

        score += 6

    elif (
        Decimal("66")
        < ind.rsi14
        <= Decimal("70")
    ):

        score += 5

    # -------------------------------------------------
    # 3. MACD / 10
    # -------------------------------------------------

    if (
        ind.macd_line
        > ind.macd_signal
    ):

        score += 7

        reasons.append(
            "MACD bullish"
        )

    else:

        score += 2

    # -------------------------------------------------
    # 4. VOLUME / 15
    # -------------------------------------------------

    if ind.volume_ratio >= Decimal("2.0"):

        score += 15

        reasons.append(
            "volume expansion"
        )

    elif ind.volume_ratio >= Decimal("1.50"):

        score += 12

    elif ind.volume_ratio >= Decimal("1.20"):

        score += 8

    elif ind.volume_ratio >= Decimal("1.00"):

        score += 5

    # -------------------------------------------------
    # 5. PULLBACK / BREAKOUT / 20
    # -------------------------------------------------

    if ind.is_pullback:

        score += 12

        reasons.append(
            "EMA21 pullback"
        )

    if ind.breakout:

        score += 8

        reasons.append(
            "resistance breakout"
        )

    elif ind.is_bullish_candle:

        score += 5

        reasons.append(
            "bullish price action"
        )

    # -------------------------------------------------
    # 6. EXECUTION QUALITY / 15
    # -------------------------------------------------

    spread = ticker.spread_percent

    if spread is None:

        score += 3

    elif spread <= Decimal("0.30"):

        score += 7

        reasons.append(
            "tight spread"
        )

    elif spread <= Decimal("0.75"):

        score += 5

    else:

        score += 2

    if (
        abs(
            ind.distance_ema21_pct
        )
        <= Decimal("3.0")
    ):

        score += 5

    elif (
        abs(
            ind.distance_ema21_pct
        )
        <= Decimal("5.0")
    ):

        score += 3

    if (
        abs(
            ind.recent_return_3
        )
        <= Decimal("2.0")
    ):

        score += 3

    return min(score, 100), reasons


def calculate_trade_levels(
    ind: IndicatorResult,
    ticker: Ticker,
) -> Tuple[
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    Decimal,
    str,
]:
    binance_current = ind.current_close
    paribu_current = getattr(ticker, "last", None)

    if paribu_current is None or paribu_current <= 0:
        raise ValueError("Invalid Paribu ticker last price.")

    if binance_current <= 0:
        raise ValueError("Invalid Binance current price.")

    # معامل التحويل الرياضي الموحد بين Binance و Paribu
    scale = paribu_current / binance_current

    # خط دفاع أخير ضد أي انحراف سعر شاذ
    if scale < Decimal("0.0001") or scale > Decimal("10000"):
        raise ValueError("Abnormal scaling factor between Binance and Paribu.")

    entry = paribu_current

    binance_stop_by_atr = (
        binance_current
        - (
            ind.atr14
            * ATR_STOP_MULTIPLIER
        )
    )

    binance_stop_by_structure = (
        ind.swing_low
        - (
            ind.atr14
            * Decimal("0.25")
        )
    )

    binance_stop = max(
        Decimal("0"),
        min(
            binance_stop_by_atr,
            binance_stop_by_structure,
        ),
    )

    if binance_stop <= 0:
        raise ValueError(
            "Invalid stop loss."
        )

    stop = binance_stop * scale

    risk = entry - stop

    if risk <= 0:
        raise ValueError(
            "Invalid risk distance."
        )

    resistance1 = ind.resistance_48 * scale
    resistance2 = max(
        ind.resistance_48,
        ind.resistance_96,
    ) * scale

    tp1_by_risk = (
        entry
        + (
            risk
            * Decimal("1.5")
        )
    )

    tp1 = min(
        tp1_by_risk,
        resistance1,
    )

    if tp1 <= entry:
        tp1 = tp1_by_risk

    tp2_by_risk = (
        entry
        + (
            risk
            * Decimal("2.5")
        )
    )

    tp2 = max(
        tp2_by_risk,
        resistance2,
    )

    if tp2 <= entry:
        raise ValueError(
            "No valid upside target."
        )

    rr = (
        tp2 - entry
    ) / risk

    if rr < MIN_RR:
        raise ValueError(
            "Risk/reward below minimum."
        )

    setup = (
        "BREAKOUT"
        if ind.breakout
        else "PULLBACK"
        if ind.is_pullback
        else "MOMENTUM"
    )

    return (
        entry,
        stop,
        tp1,
        tp2,
        rr,
        setup,
    )


def cooldown_allowed(
    symbol: str,
    score: int,
    state: Dict[str, float],
) -> bool:

    key = (
        f"{symbol}:"
        f"{score // 5}"
    )

    now = time.time()

    last = state.get(key)

    if (
        last is not None
        and (
            now - last
            < COOLDOWN_SECONDS
        )
    ):

        return False

    state[key] = now

    return True


class MarketScanner:

    def __init__(
        self,
        top_n: int = TOP_N,
    ):

        self.top_n = top_n

    def scan_market(
        self,
    ) -> Tuple[
        List[Opportunity],
        ScanStats,
    ]:

        stats = ScanStats()

        snapshot = (
            get_market_snapshot()
        )

        stats.total_markets = len(
            snapshot
        )

        # Sort by actual quote turnover.
        tickers = sorted(
            snapshot.values(),
            key=lambda x: (
                x.quote_volume
                if x.quote_volume
                else Decimal("0")
            ),
            reverse=True,
        )

        state = load_state()

        candidates: List[
            Opportunity
        ] = []

        for ticker in tickers:

            # -----------------------------------------
            # Liquidity
            # -----------------------------------------

            if (
                ticker.quote_volume
                is None
                or ticker.quote_volume
                < MIN_QUOTE_VOLUME_TL
            ):

                stats.liquidity_fail += 1

                continue

            stats.liquidity_pass += 1

            # -----------------------------------------
            # Spread
            # -----------------------------------------

            if (
                ticker.spread_percent
                is not None
                and ticker.spread_percent
                > MAX_SPREAD_PERCENT
            ):

                stats.spread_fail += 1

                continue

            stats.spread_pass += 1

            # -----------------------------------------
            # Technical request limit
            # -----------------------------------------

            if (
                stats.technical_attempted
                >= MAX_TECHNICAL_MARKETS
            ):

                break

            stats.technical_attempted += 1

            # -----------------------------------------
            # Candles
            # -----------------------------------------

            try:

                df15 = fetch_candles(
                    ticker.symbol,
                    "15m",
                    250,
                )

            except Exception as exc:

                stats.candle_fail += 1

                print(
                    f"[CANDLE ERROR] "
                    f"{ticker.symbol}: "
                    f"{exc}"
                )

                continue

            stats.candle_success += 1

            # -----------------------------------------
            # Indicators
            # -----------------------------------------

            ind = analyze_symbol(
                df15
            )

            if ind is None:

                stats.indicator_fail += 1

                continue

            stats.indicator_success += 1

            # -----------------------------------------
            # HARD FILTER: RSI
            # -----------------------------------------

            if (
                ind.rsi14
                > RSI_HARD_MAX
            ):

                stats.rsi_fail += 1

                continue

            # -----------------------------------------
            # HARD FILTER: TREND
            # -----------------------------------------

            if not ind.is_uptrend:

                stats.trend_fail += 1

                continue

            # -----------------------------------------
            # HARD FILTER: FOMO
            # -----------------------------------------

            if (
                ind.recent_return_3
                >= MAX_3_CANDLE_RETURN
                or ind.recent_return_12
                >= MAX_12_CANDLE_RETURN
            ):

                stats.fomo_fail += 1

                continue

            # -----------------------------------------
            # HARD FILTER: VOLATILITY
            # -----------------------------------------

            atr_percent = (
                ind.atr14
                / ind.current_close
                * 100
            )

            if (
                atr_percent
                < MIN_ATR_PERCENT
                or atr_percent
                > MAX_ATR_PERCENT
            ):

                stats.volatility_fail += 1

                continue

            # -----------------------------------------
            # HARD FILTER: SETUP
            # -----------------------------------------

            if not (
                ind.is_pullback
                or ind.breakout
                or (
                    ind.is_bullish_candle
                    and ind.is_above_ema21
                )
            ):

                stats.setup_fail += 1

                continue

            # -----------------------------------------
            # SCORE
            # -----------------------------------------

            score, reasons = score_candidate(
                ind,
                ticker,
            )

            # -----------------------------------------
            # LEVELS
            # -----------------------------------------

            try:

                (
                    entry,
                    stop,
                    tp1,
                    tp2,
                    rr,
                    setup,
                ) = calculate_trade_levels(
                    ind,
                    ticker,
                )

            except ValueError as exc:

                stats.rr_fail += 1

                print(
                    f"[LEVEL ERROR] "
                    f"{ticker.symbol}: "
                    f"{exc}"
                )

                continue

            # -----------------------------------------
            # SCORE THRESHOLD
            # -----------------------------------------

            if score < MIN_ACTION_SCORE:

                stats.score_fail += 1

                continue

            stats.candidates_before_rank += 1

            step = precision_for_price(
                entry
            )

            paribu_current = getattr(ticker, "last", None)
            if paribu_current is None or paribu_current <= 0:
                paribu_current = entry

            opportunity = Opportunity(

                symbol=ticker.symbol,

                score=score,

                strength=(
                    strength_from_score(
                        score
                    )
                ),

                setup=setup,

                current_price=quantize_price(
                    paribu_current,
                    step,
                ),

                entry_price=quantize_price(
                    entry,
                    step,
                ),

                stop_loss=quantize_price(
                    stop,
                    step,
                ),

                tp_1=quantize_price(
                    tp1,
                    step,
                ),

                tp_2=quantize_price(
                    tp2,
                    step,
                ),

                rr=rr.quantize(
                    Decimal("0.01")
                ),

                rsi=ind.rsi14.quantize(
                    Decimal("0.1")
                ),

                atr_percent=(
                    atr_percent.quantize(
                        Decimal("0.01")
                    )
                ),

                volume_ratio=(
                    ind.volume_ratio.quantize(
                        Decimal("0.01")
                    )
                ),

                reason=" | ".join(
                    reasons[:6]
                ),
            )

            candidates.append(
                opportunity
            )

        # -----------------------------------------
        # Ranking
        # -----------------------------------------

        candidates.sort(
            key=lambda x: (
                x.score,
                x.rr,
                x.volume_ratio,
            ),
            reverse=True,
        )

        final: List[
            Opportunity
        ] = []

        for opportunity in candidates:

            if len(final) >= self.top_n:
                break

            if cooldown_allowed(
                opportunity.symbol,
                opportunity.score,
                state,
            ):

                final.append(
                    opportunity
                )

        save_state(state)

        return final, stats
