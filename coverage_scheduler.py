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

    def prioritize(
        self, items: Iterable[Any], stage: str, symbol: Callable,
        priority_symbols: Iterable[str], budget: int,
    ) -> tuple[list, list[str]]:
        """Reserve at least half the slots for least-recently-attempted work.

        Priority changes scheduling only. Missing symbols consume no slots;
        unused priority capacity returns to normal coverage. The caller still
        applies its unchanged request cap and all market/strategy gates.
        """
        base = self.order(items, stage, symbol)
        by_symbol = {symbol(item): item for item in base}
        selected = []
        selected_set = set()
        priority_budget = max(0, int(budget)) // 2
        for name in priority_symbols:
            if len(selected) >= priority_budget:
                break
            if name in by_symbol and name not in selected_set:
                selected.append(name)
                selected_set.add(name)
        ordered = [by_symbol[name] for name in selected]
        ordered.extend(item for item in base if symbol(item) not in selected_set)
        return ordered, selected
