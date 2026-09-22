"""Version observations without rewriting the deployed baseline's records."""
import os

VERSION = "shadow_integrity_v2"


def ensure_cohort(state, now):
    current = state.get("research_cohort")
    if current:
        if not isinstance(current, dict) or not isinstance(current.get("id"), str):
            raise ValueError("Invalid research cohort")
        if current.get("version") == VERSION:
            return current
        state.setdefault("research_cohort_history", []).append({**current, "ended_at": now})
    previous = state.get("scan_diagnostics", [])
    cohort = {
        "id": f"{VERSION}:{now}", "version": VERSION, "started_at": now,
        "code_sha": os.getenv("GITHUB_SHA"), "run_id": os.getenv("GITHUB_RUN_ID"),
        "previous_completed_at": previous[-1].get("finished_at") if previous else None,
        "legacy_results_comparable": False,
    }
    state["research_cohort"] = cohort
    return cohort
