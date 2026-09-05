from __future__ import annotations

"""Read-only Paribu connectivity and candle preflight test.

This script never places orders and never sends Telegram messages.
It verifies that the current market-data layer can read BTC_TL candles from
Paribu for the three intraday timeframes used by the sniper.
"""

import sys
import traceback

from market_data import fetch_candles, get_market_snapshot, normalize_symbol


SYMBOL = "BTC_TL"
TIMEFRAMES = ("15m", "1h", "4h")


def main() -> int:
    print("=== Paribu Spot Sniper preflight ===")
    print(f"Symbol: {normalize_symbol(SYMBOL)}")
    print("Mode: READ ONLY — no orders, no Telegram")
    print()

    try:
        snapshot = get_market_snapshot()
        btc = next((x for x in snapshot if normalize_symbol(x.symbol) == SYMBOL), None)
        if btc is None:
            print("[FAIL] BTC_TL was not found in Paribu ticker data.")
            return 1

        print("[PASS] Paribu ticker is reachable.")
        print(f"       BTC_TL last={btc.last} quote_volume_tl={btc.quote_volume_tl}")
        print()

        for timeframe in TIMEFRAMES:
            df = fetch_candles(SYMBOL, timeframe, 250)
            source = str(df.attrs.get("source", "")).upper()
            provider = str(df.attrs.get("provider", ""))
            if source != "PARIBU":
                print(f"[FAIL] {timeframe}: unexpected source={source!r}")
                return 1
            if len(df) < 205:
                print(f"[FAIL] {timeframe}: only {len(df)} usable closed candles.")
                return 1
            print(
                f"[PASS] {timeframe}: {len(df)} closed candles | "
                f"source={source} | provider={provider} | "
                f"last_close={df['close'].iloc[-1]}"
            )

        print()
        print("=== PREFLIGHT PASSED ===")
        print("Paribu ticker + 15m + 1h + 4h candle reads are working.")
        return 0

    except Exception as exc:  # noqa: BLE001 - preflight must show the exact cause
        print()
        print("=== PREFLIGHT FAILED ===")
        print(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        print()
        print("No trade or Telegram action was performed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
