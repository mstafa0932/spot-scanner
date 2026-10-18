from __future__ import annotations

"""Read-only Paribu connectivity and candle preflight test.

This script never places orders and never sends Telegram messages.
It verifies that the current market-data layer can read BTC_TL candles from
Paribu for the three intraday timeframes used by the sniper.
"""

import traceback

from market_data import (
    PARIBU_NATIVE_OHLC_URL,
    fetch_candles,
    get_market_snapshot,
    normalize_symbol,
)

SYMBOL = "BTC_TL"
TIMEFRAMES = ("15m", "1h", "4h")


def main() -> int:
    print("=== Paribu Spot Sniper preflight ===")
    print(f"Symbol: {normalize_symbol(SYMBOL)}")
    print("Mode: READ ONLY — no orders, no Telegram")
    print()

    try:
        # ------------------------------------------------------------
        # 1) Test Paribu ticker
        # ------------------------------------------------------------
        snapshot = get_market_snapshot()

        btc = snapshot.get(SYMBOL)
        if btc is None:
            print("[FAIL] BTC_TL was not found in Paribu ticker data.")
            return 1

        print("[PASS] Paribu ticker is reachable.")
        print(
            f"       BTC_TL last={btc.last} "
            f"quote_volume={btc.quote_volume}"
        )
        print()

        # ------------------------------------------------------------
        # 2) Read the same native OHLC service as the production data layer.
        #    No legacy endpoint success can substitute for candle integrity.
        # ------------------------------------------------------------
        print(f"[INFO] Intraday candle endpoint: {PARIBU_NATIVE_OHLC_URL}")
        print()

        # ------------------------------------------------------------
        # 3) Test all scanner timeframes
        # ------------------------------------------------------------
        for timeframe in TIMEFRAMES:
            df = fetch_candles(
                SYMBOL,
                timeframe,
                250,
            )

            source = str(df.attrs.get("source", "")).upper()
            provider = str(df.attrs.get("provider", ""))

            if source != "PARIBU":
                print(
                    f"[FAIL] {timeframe}: "
                    f"unexpected source={source!r}"
                )
                return 1

            if df.attrs.get("candle_endpoint") != PARIBU_NATIVE_OHLC_URL:
                print(f"[FAIL] {timeframe}: wrong candle endpoint")
                return 1
            if "is_authentic" not in df or not df["is_authentic"].all():
                print(f"[FAIL] {timeframe}: local synthetic candles present")
                return 1
            if len(df) < 205:
                print(
                    f"[FAIL] {timeframe}: "
                    f"only {len(df)} usable closed candles."
                )
                return 1

            print(
                f"[PASS] {timeframe}: {len(df)} closed candles | "
                f"source={source} | "
                f"provider={provider} | "
                f"exchange_zero_volume={df.attrs.get('published_zero_volume_count', 0)} | "
                f"last_close={df['close'].iloc[-1]}"
            )

        print()
        print("=== PREFLIGHT PASSED ===")
        print("Paribu ticker + 15m + 1h + 4h candle reads are working.")
        print("The scanner can proceed to the main scan.")
        return 0

    except Exception as exc:
        print()
        print("=== PREFLIGHT FAILED ===")
        print(f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        print()
        print("No trade or Telegram action was performed.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
