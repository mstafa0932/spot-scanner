from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

import requests

from indicator_engine import calculate_indicators
from market_data import (
    ParibuDataError,
    Ticker,
    fetch_candles,
    get_market_snapshot,

)

# ============================================================
# CONFIGURATION & CONSTANTS
# ============================================================

STATE_FILE = "scanner_state.json"
MAX_SIGNALS_PER_RUN = 3

# Execution Rules (Paribu Spot)
MAX_ALLOWED_SPREAD_PCT = Decimal("0.80")  # Max allowed spread: 0.80%
MIN_REQUIRED_TP1_PCT = Decimal("1.80")    # Min price distance to TP1: 1.80%
MIN_REQUIRED_RR = Decimal("1.50")         # Min Risk-to-Reward ratio

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


# ============================================================
# TELEGRAM SENDER
# ============================================================

def send_telegram_message(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        LOGGER.error("Telegram credentials missing in environment variables.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        return resp.status_code == 200
    except requests.RequestException as exc:
        LOGGER.error(f"Failed to send Telegram message: {exc}")
        return False


# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state() -> dict[str, Any]:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            LOGGER.warning(f"Could not load state file: {exc}")
    return {"sent_signals": {}}


def save_state(state: dict[str, Any]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
    except Exception as exc:
        LOGGER.error(f"Could not save state file: {exc}")


# ============================================================
# EXECUTION EVALUATION
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


def evaluate_execution(
    ticker: Ticker,
    binance_close: Decimal,
    stop_loss_raw: Decimal,
    tp1_raw: Decimal,
    tp2_raw: Decimal,
) -> ExecutionResult:
    # Use real local ask price for entry
    entry_price = ticker.ask if ticker.ask and ticker.ask > 0 else ticker.last

    if ticker.spread_percent is None:
        return ExecutionResult(
            is_executable=False,
            reject_reason="سبريد غير معروف أو بيانات دفتر الطلبات غائبة",
            entry_price=entry_price,
            stop_loss=stop_loss_raw,
            tp1=tp1_raw,
            tp2=tp2_raw,
            rr_ratio=Decimal("0"),
            spread_pct=Decimal("99"),
            tp1_pct=Decimal("0"),
        )

    spread_pct = ticker.spread_percent

    # 1. Spread Check
    if spread_pct > MAX_ALLOWED_SPREAD_PCT:
        return ExecutionResult(
            is_executable=False,
            reject_reason=f"السبريد مرتفع جداً ({spread_pct:.2f}% > {MAX_ALLOWED_SPREAD_PCT}%)",
            entry_price=entry_price,
            stop_loss=stop_loss_raw,
            tp1=tp1_raw,
            tp2=tp2_raw,
            rr_ratio=Decimal("0"),
            spread_pct=spread_pct,
            tp1_pct=Decimal("0"),
        )

    # Re-calculate TP and SL offsets based on Paribu execution price
    price_diff_sl = binance_close - stop_loss_raw
    price_diff_tp1 = tp1_raw - binance_close
    price_diff_tp2 = tp2_raw - binance_close

    local_sl = entry_price - price_diff_sl
    local_tp1 = entry_price + price_diff_tp1
    local_tp2 = entry_price + price_diff_tp2

    if local_sl <= 0 or local_tp1 <= entry_price:
        return ExecutionResult(
            is_executable=False,
            reject_reason="أهداف غير منطقية بعد تعديل السعر المحلي",
            entry_price=entry_price,
            stop_loss=local_sl,
            tp1=local_tp1,
            tp2=local_tp2,
            rr_ratio=Decimal("0"),
            spread_pct=spread_pct,
            tp1_pct=Decimal("0"),
        )

    tp1_pct = ((local_tp1 - entry_price) / entry_price) * Decimal("100")
    risk = entry_price - local_sl
    reward = local_tp1 - entry_price

    rr_ratio = reward / risk if risk > 0 else Decimal("0")

    # 2. Min Distance to TP1 Check
    if tp1_pct < MIN_REQUIRED_TP1_PCT:
        return ExecutionResult(
            is_executable=False,
            reject_reason=f"الربح المستهدف لـ TP1 غير كافٍ ({tp1_pct:.2f}% < {MIN_REQUIRED_TP1_PCT}%)",
            entry_price=entry_price,
            stop_loss=local_sl,
            tp1=local_tp1,
            tp2=local_tp2,
            rr_ratio=rr_ratio,
            spread_pct=spread_pct,
            tp1_pct=tp1_pct,
        )

    # 3. Risk-to-Reward Ratio Check
    if rr_ratio < MIN_REQUIRED_RR:
        return ExecutionResult(
            is_executable=False,
            reject_reason=f"نسبة المخاطرة إلى المكافأة غير كافية ({rr_ratio:.2f} < {MIN_REQUIRED_RR})",
            entry_price=entry_price,
            stop_loss=local_sl,
            tp1=local_tp1,
            tp2=local_tp2,
            rr_ratio=rr_ratio,
            spread_pct=spread_pct,
            tp1_pct=tp1_pct,
        )

    return ExecutionResult(
        is_executable=True,
        reject_reason=None,
        entry_price=entry_price,
        stop_loss=local_sl,
        tp1=local_tp1,
        tp2=local_tp2,
        rr_ratio=rr_ratio,
        spread_pct=spread_pct,
        tp1_pct=tp1_pct,
    )


# ============================================================
# SCANNER CORE
# ============================================================

def format_signal_message(
    symbol: str,
    exec_res: ExecutionResult,
    indicators: dict[str, Any],
    signal_num: int,
) -> str:
    return (
        f"🎯 <b>SPOT OPPORTUNITY #{signal_num}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{symbol}</b>\n"
        f"📡 <b>مصدر الشموع:</b> BINANCE\n"
        f"💵 <b>سعر الدخول (Paribu Ask):</b> <code>{exec_res.entry_price:.6f}</code>\n"
        f"🛑 <b>وقف الخسارة:</b> <code>{exec_res.stop_loss:.6f}</code>\n"
        f"🎯 <b>TP1 (+{exec_res.tp1_pct:.2f}%):</b> <code>{exec_res.tp1:.6f}</code>\n"
        f"🚀 <b>TP2:</b> <code>{exec_res.tp2:.6f}</code>\n"
        f"📐 <b>R:R (محلي):</b> 1:{exec_res.rr_ratio:.2f}\n"
        f"📊 <b>السبريد المحلي:</b> <code>{exec_res.spread_pct:.2f}%</code>\n\n"
        f"📈 <b>RSI:</b> {indicators.get('rsi', 0):.1f}\n"
        f"📉 <b>ATR%:</b> {indicators.get('atr_pct', 0):.2f}%\n"
        f"💡 <b>السبب:</b> {indicators.get('reason', 'إشارة مكتملة الشروط')}\n\n"
        f"⚠️ <b>تنبيه:</b> توصية فحص وتحليل فني - تنفيذ يدوي على منصة Paribu."
    )


def run_scanner() -> None:
    LOGGER.info("Starting market scan...")

    try:
        snapshot = get_market_snapshot()
    except ParibuDataError as exc:
        LOGGER.error(f"Could not fetch Paribu market snapshot: {exc}")
        return

    state = load_state()
    sent_signals = state.get("sent_signals", {})

    signals_found = 0

    for symbol, ticker in snapshot.items():
        if signals_found >= MAX_SIGNALS_PER_RUN:
            LOGGER.info(f"Reached max signals limit ({MAX_SIGNALS_PER_RUN}) for this run.")
            break

        # Fetch candles from Binance
        try:
            df = fetch_candles(symbol, resolution="15m", limit=250)
        except Exception as exc:
            LOGGER.debug(f"Skipping {symbol}: candle fetch error - {exc}")
            continue

        # Run indicators calculation (using closed candle i=-2 internally)
        ind = calculate_indicators(df)
        if not ind.get("is_valid_setup", False):
            continue

        binance_close = Decimal(str(ind["close"]))
        stop_loss_raw = Decimal(str(ind["stop_loss"]))
        tp1_raw = Decimal(str(ind["tp1"]))
        tp2_raw = Decimal(str(ind["tp2"]))

        # Filter execution on Paribu real prices
        exec_res = evaluate_execution(
            ticker=ticker,
            binance_close=binance_close,
            stop_loss_raw=stop_loss_raw,
            tp1_raw=tp1_raw,
            tp2_raw=tp2_raw,
        )

        if not exec_res.is_executable:
            LOGGER.info(f"Rejected {symbol}: {exec_res.reject_reason}")
            continue

        # Signal Cooldown check
        if symbol in sent_signals:
            LOGGER.info(f"Skipping {symbol}: already sent recently.")
            continue

        signals_found += 1
        msg = format_signal_message(symbol, exec_res, ind, len(sent_signals) + 1)

        if send_telegram_message(msg):
            LOGGER.info(f"Successfully sent signal for {symbol}")
            sent_signals[symbol] = int(df.iloc[-2]["timestamp"])
            state["sent_signals"] = sent_signals
            save_state(state)
        else:
            LOGGER.error(f"Failed to send Telegram signal for {symbol}")

    LOGGER.info("Scan completed successfully.")


if __name__ == "__main__":
    run_scanner()
