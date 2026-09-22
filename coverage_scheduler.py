"""Fair polling under the existing request budgets; no strategy decisions."""
from typing import Any, Callable, Iterable


class CoverageCycle:
    """Least-recently-attempted order, independently for books and technicals.

    The monotonic cycle counter survives restart and does not depend on wall
    clock movement. Failed requests count as attempts so one broken market
    cannot monopolize a budget. Ties retain the caller's volume-ranked order.
    """

    def __init__(self, state: dict, symbols: Iterable[str]):
        generation = state.get("generation", 0)
        if type(generation) is not int or generation < 0:
            raise ValueError("Invalid coverage generation")
        markets = set(symbols)
        histories = {}
        for stage in ("orderbooks", "technicals"):
            history = state.get(stage, {})
            if not isinstance(history, dict) or any(
                not isinstance(symbol, str) or type(at) is not int or not 0 <= at <= generation
                for symbol, at in history.items()
            ):
                raise ValueError("Invalid coverage history: " + stage)
            histories[stage] = {symbol: at for symbol, at in history.items() if symbol in markets}
        state.update(histories)
        state["generation"] = generation + 1
        self.state = state

    def order(self, items: Iterable[Any], stage: str, symbol: Callable) -> list:
        return sorted(items, key=lambda item: self.state[stage].get(symbol(item), 0))

    def attempted(self, stage: str, symbol: str) -> None:
        self.state[stage][symbol] = self.state["generation"]
