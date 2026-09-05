from __future__ import annotations

"""Read-only Paribu connectivity and candle preflight test.

This script never places orders and never sends Telegram messages.
It verifies that the current market-data layer can read BTC_TL candles from
Paribu for the three intraday timeframes used by the sniper.
"""

import time
import traceback

from market_data import (
    PARIBU_CHART_HISTORY_URL,
    fetch_candles,
    get_market_snapshot,
    normalize_symbol,
    SESSION,
)


SYMBOL = "BTC_TL"
TIMEFRAMES = ("15m", "1h", "4h")


def main() -> int:
    print("=== Paribu Spot Sniper preflight ===")
    print(f"Symbol: {normalize_symbol(SYMBOL)}")
    print("Mode: READ ONLY — no orders, no Telegram")
    print()

    try:
        snapshot = get_market_snapshot()
        btc = snapshot.get(SYMBOL)
        if btc is None:
            print("[FAIL] BTC_TL was not found in Paribu ticker data.")
            return 1

        print("[PASS] Paribu ticker is reachable.")
        print(f"       BTC_TL last={btc.last} quote_volume_tl={btc.quote_volume_tl}")
        print()

        # Direct request-shape check. This is intentionally read-only and
        # proves that GitHub Actions is sending the exact advanced fields that
        # Paribu's current error message requires.
        end_s = int(time.time())
        request_params = {
            "type": "advanced",
            "symbol": "btc_tl",
            "resolution": "15",
            "from": end_s - 15 * 60 * 250,
            "to": end_s,
        }
        response = SESSION.get(
            PARIBU_CHART_HISTORY_URL,
            params=request_params,
            timeout=12,
        )
        print(f"[INFO] chart/history HTTP={response.status_code}")
        print(f"       request={response.url}")
        if response.status_code != 200:
            print(f"       body={response.text[:500]}")
            print("[FAIL] Paribu rejected the required advanced request shape.")
            return 1
        print("[PASS] Paribu accepted the advanced chart request shape.")
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
