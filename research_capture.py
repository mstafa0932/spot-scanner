"""Bounded, opt-in archive of public inputs already fetched by the scanner.

No provider calls, environment dumps, account data or strategy decisions. Each
gzip member holds one checksummed JSON line. Failure is observational only.
"""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import time

LOGGER = logging.getLogger("paribu_research_capture")
BOOK_BUDGET = 128 * 1024
CANDLE_BUDGET = 384 * 1024


class ResearchCapture:
    """Capture public numeric inputs without modifying their objects or gates."""

    def __init__(self, enabled=False, directory=None, run_id=None, code_sha=None):
        self.enabled = enabled
        self.directory = Path(directory) if directory is not None else None
        self.run_id, self.code_sha = run_id, code_sha
        self.members = []
        self.sizes = Counter()
        self.counts = Counter()

    @classmethod
    def from_env(cls, now):
        """Require explicit shadow-only opt-in and a deadline within seven days."""
        try:
            until = os.getenv("RESEARCH_CAPTURE_UNTIL", "")
            directory = os.getenv("RESEARCH_CAPTURE_DIR", "")
            if not until or not directory or os.getenv("SHADOW_MODE", "true").lower().strip() != "true":
                return cls()
            end = datetime.fromisoformat(until.replace("Z", "+00:00"))
            if end.tzinfo is None or not 0 < end.timestamp() - now <= 7 * 86400:
                return cls()
            return cls(True, directory, os.getenv("GITHUB_RUN_ID"), os.getenv("GITHUB_SHA"))
        except (ValueError, TypeError, OSError):
            return cls()

    def _record(self, kind, payload, category="books"):
        """Keep complete records only; reserve the book budget independently."""
        if not self.enabled:
            return
        try:
            record = {"schema_version": 1, "kind": kind, "source": "PARIBU",
                      "available_at": int(time.time()), "run_id": self.run_id,
                      "code_sha": self.code_sha, "payload": payload}
            raw = json.dumps(record, sort_keys=True, separators=(",", ":"),
                             allow_nan=False).encode()
            wrapped = {"sha256": hashlib.sha256(raw).hexdigest(), "record": record}
            member = gzip.compress(json.dumps(wrapped, sort_keys=True, separators=(",", ":"),
                                              allow_nan=False).encode() + b"\n", mtime=0)
            limit = BOOK_BUDGET if category == "books" else CANDLE_BUDGET
            if self.sizes[category] + len(member) > limit:
                self.counts["omitted_budget:" + kind] += 1
                return
            self.members.append(member)
            self.sizes[category] += len(member)
            self.counts["captured:" + kind] += 1
        except Exception as exc:
            self.counts["error:" + kind] += 1
            LOGGER.warning("Research capture record failed: %s", type(exc).__name__)

    def tickers(self, snapshot):
        """Whitelist public ticker fields; never serialize arbitrary attributes."""
        if not self.enabled:
            return
        try:
            fields = ("last", "bid", "ask", "volume", "quote_volume", "change_percent")
            self._record("tickers", {symbol: {k: str(getattr(t, k)) if getattr(t, k, None) is not None else None
                                              for k in fields} for symbol, t in snapshot.items()})
        except Exception as exc:
            self.counts["error:tickers"] += 1
            LOGGER.warning("Research ticker capture failed: %s", type(exc).__name__)

    def book(self, book, stage):
        """Archive the supplied L2 snapshot, including rejected and fresh books."""
        if not self.enabled:
            return
        try:
            fields = ("best_bid", "best_ask", "spread_percent", "bid_notional", "ask_notional",
                      "imbalance_ratio", "top3_bid_share", "top3_ask_share",
                      "largest_bid_wall_share", "largest_ask_wall_share", "timestamp")
            payload = {k: str(getattr(book, k)) if getattr(book, k, None) is not None else None for k in fields}
            payload.update(symbol=book.symbol, stage=stage,
                           bids=[[str(p), str(q)] for p, q in book.bids],
                           asks=[[str(p), str(q)] for p, q in book.asks])
            self._record("book", payload)
        except Exception as exc:
            self.counts["error:book"] += 1
            LOGGER.warning("Research book capture failed: %s", type(exc).__name__)

    def candles(self, symbol, resolution, frame):
        """Preserve all fetched closed authentic rows, without repairing data."""
        if not self.enabled:
            return
        try:
            now = int(time.time())
            seconds = {"15m": 900, "1h": 3600, "4h": 14400}[resolution]
            flags = frame["is_authentic"].tolist()
            if (str(frame.attrs.get("source", "")).upper() != "PARIBU"
                    or frame.attrs.get("resolution") != resolution
                    or not flags or not all(type(v).__name__ in {"bool", "bool_"} and bool(v) for v in flags)
                    or any(int(t) + seconds > now for t in frame["timestamp"])):
                self.counts["omitted_integrity:candles"] += 1
                return
            fields = ("open", "high", "low", "close", "volume")
            rows = [{"timestamp": int(r["timestamp"]), **{k: str(r[k]) for k in fields}, "is_authentic": True}
                    for r in frame.to_dict("records")]
            for row in rows:
                if not all(Decimal(row[k]).is_finite() for k in fields):
                    raise ValueError("Nonfinite candle input")
            attrs = {k: frame.attrs.get(k) for k in
                     ("source", "resolution", "symbol", "candle_endpoint", "published_zero_volume_count")}
            self._record("candles", {"symbol": symbol, "resolution": resolution,
                                      "attributes": attrs, "rows": rows}, "candles")
        except Exception as exc:
            self.counts["error:candles"] += 1
            LOGGER.warning("Research candle capture failed: %s", type(exc).__name__)

    def finish(self):
        """Publish a bounded artifact; archive failures never affect readiness."""
        if not self.enabled:
            return {"enabled": False}
        summary = {"enabled": True, "schema_version": 1, "run_id": self.run_id,
                   "code_sha": self.code_sha, "counts": dict(self.counts),
                   "compressed_bytes": sum(self.sizes.values()),
                   "maximum_compressed_bytes": BOOK_BUDGET + CANDLE_BUDGET,
                   "scope": "existing fetched inputs only; not all-market candle coverage",
                   "is_predictor": False, "is_trade": False,
                   "limitations": ["No new provider calls or raw BTC-regime bars.",
                                   "Book-rejected markets have books but no newly fetched candles.",
                                   "Budget omissions are missing evidence, never rejected opportunities.",
                                   "available_at is conservative capture time, not exchange event time."]}
        try:
            raw = b"".join(self.members)
            assert len(raw) <= BOOK_BUDGET + CANDLE_BUDGET
            summary["data_sha256"] = hashlib.sha256(raw).hexdigest()
            summary["status"] = "partial" if any(k.startswith(("omitted", "error")) for k in self.counts) else "captured"
            self.directory.mkdir(parents=True, exist_ok=True)
            temp = self.directory / "inputs.jsonl.gz.tmp"
            temp.write_bytes(raw)
            temp.replace(self.directory / "inputs.jsonl.gz")
            temp = self.directory / "manifest.json.tmp"
            temp.write_text(json.dumps(summary, sort_keys=True))
            temp.replace(self.directory / "manifest.json")
        except Exception as exc:
            summary["status"] = "error:" + type(exc).__name__
            LOGGER.warning("Research capture archive failed: %s", type(exc).__name__)
        LOGGER.info("RESEARCH_CAPTURE | %s", json.dumps(summary, sort_keys=True))
        return summary
