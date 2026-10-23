from decimal import Decimal
from market_data import Ticker
from coverage_scheduler import CoverageCycle
import scanner


def _ticker(symbol, last, volume):
    return Ticker(
        symbol=symbol,
        last=Decimal(last),
        quote_volume=Decimal(volume),
    )


def _observe(state, tickers, now):
    scheduler = CoverageCycle(
        state["coverage_scheduler"],
        [item.symbol for item in tickers],
    )
    return scanner._hot_radar_order(tickers, state, scheduler, now)


def test_jasmy_crossing_and_sent_low_liquidity_are_recorded_without_signals():
    state = scanner._empty_state()
    at = 1792761200
    first = [
        _ticker("JASMY_TL", "0.2290", "4195975.4275"),
        _ticker("SENT_TL", "1.1200", "4545076.5648"),
    ]
    _ordered, initial = _observe(state, first, at)
    research = state["ticker_radar"]
    near = {item["symbol"]: item for item in research["near_liquidity_research"]}
    assert "JASMY_TL" in near and "SENT_TL" in near
    assert research["recent_liquidity_crossings"] == []
    assert initial["selected_priority_symbols"] == []

    second = [
        _ticker("JASMY_TL", "0.2360", "7828566.2868"),
        _ticker("SENT_TL", "1.1300", "4552224.7545"),
    ]
    _ordered, promoted = _observe(state, second, at + 600)
    research = state["ticker_radar"]
    crossing = research["recent_liquidity_crossings"]
    assert len(crossing) == 1
    assert crossing[0]["symbol"] == "JASMY_TL"
    assert crossing[0]["prior_quote_volume_tl"] == "4195975.4275"
    assert crossing[0]["current_quote_volume_tl"] == "7828566.2868"
    assert "JASMY_TL" in promoted["selected_priority_symbols"]
    assert "SENT_TL" not in promoted["selected_priority_symbols"]
    assert research["liquidity_research_basis"].startswith("rolling_24h")
    assert state["shadow_sent_signals"] == {}
    assert state["shadow_paper_signals"] == {}

    _observe(state, second, at + 1200)
    assert len(state["ticker_radar"]["recent_liquidity_crossings"]) == 1
    _observe(state, second, at + 24 * 60 * 60 + 601)
    assert state["ticker_radar"]["recent_liquidity_crossings"] == []


def test_below_liquidity_limit_never_becomes_trading_candidate():
    state = scanner._empty_state()
    at = 1792761200
    tickers = [_ticker("SENT_TL", "1.1471", "4467749.28")]
    _ordered, hot = _observe(state, tickers, at)
    assert hot["selected_priority_symbols"] == []
    assert hot["eligible_count"] == 0
    assert len(state["ticker_radar"]["near_liquidity_research"]) == 1
    assert not state["shadow_daily_alerts"]
