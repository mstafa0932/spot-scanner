"""Read public-input artifacts without extraction, network, or scanner execution.

Digest validation proves byte consistency, not an exchange signature, fill,
profitable opportunity, full-market coverage, or complete strategy evidence.
"""
import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

MAX_COMPRESSED = 512 * 1024
MAX_RAW = 128 * 1024 * 1024
INTERVALS = {"15m": 900, "1h": 3600, "4h": 14400}


def read_capture(path, expected_run=None, expected_code=None):
    """Return verified records and an audit; fail on malformed or changed data."""
    path = Path(path)
    if path.is_dir():
        if (path / "manifest.json").stat().st_size > 65536:
            raise ValueError("Oversized manifest")
        if (path / "inputs.jsonl.gz").stat().st_size > MAX_COMPRESSED:
            raise ValueError("Oversized compressed capture")
        manifest_bytes = (path / "manifest.json").read_bytes()
        data = (path / "inputs.jsonl.gz").read_bytes()
    else:
        with zipfile.ZipFile(path) as archive:
            found = {}
            for name, limit in (("manifest.json", 65536), ("inputs.jsonl.gz", MAX_COMPRESSED)):
                matches = [x for x in archive.infolist() if Path(x.filename).name == name]
                if len(matches) != 1 or matches[0].file_size > limit:
                    raise ValueError("Missing, duplicate or oversized artifact member: " + name)
                found[name] = archive.read(matches[0])
            manifest_bytes, data = found["manifest.json"], found["inputs.jsonl.gz"]
    manifest = json.loads(manifest_bytes)
    if manifest.get("schema_version") != 1 or manifest.get("enabled") is not True:
        raise ValueError("Unsupported capture manifest")
    if (manifest.get("compressed_bytes") != len(data)
            or manifest.get("data_sha256") != hashlib.sha256(data).hexdigest()):
        raise ValueError("Archive digest or size mismatch")
    for key, expected in (("run_id", expected_run), ("code_sha", expected_code)):
        if expected is not None and str(manifest.get(key)) != str(expected):
            raise ValueError("Unexpected " + key)
    code = manifest.get("code_sha")
    if code is not None and not re.fullmatch(r"[0-9a-f]{40}", code):
        raise ValueError("Invalid checkout SHA")
    with gzip.GzipFile(fileobj=io.BytesIO(data)) as compressed:
        raw = compressed.read(MAX_RAW + 1)
    if len(raw) > MAX_RAW:
        raise ValueError("Oversized decompressed capture")
    records, counts, frames, stages = [], Counter(), defaultdict(set), Counter()
    for line in raw.splitlines():
        envelope = json.loads(line)
        record = envelope["record"]
        canonical = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if hashlib.sha256(canonical).hexdigest() != envelope.get("sha256"):
            raise ValueError("Record digest mismatch")
        if record.get("schema_version") != 1 or record.get("source") != "PARIBU":
            raise ValueError("Unknown record schema/source")
        if type(record.get("available_at")) is not int or record["available_at"] < 0:
            raise ValueError("Invalid availability clock")
        if any(record.get(k) != manifest.get(k) for k in ("run_id", "code_sha", "trigger_sha")):
            raise ValueError("Manifest/record provenance mismatch")
        kind, payload = record["kind"], record["payload"]
        if kind not in {"tickers", "book", "candles"}:
            raise ValueError("Unsupported record kind")
        counts[kind] += 1
        if kind == "book":
            stages[payload["stage"]] += 1
        if kind == "candles":
            resolution, rows = payload["resolution"], payload["rows"]
            seconds = INTERVALS[resolution]
            if not rows or payload["attributes"].get("source") != "PARIBU":
                raise ValueError("Unverified candle source")
            timestamps = []
            for row in rows:
                timestamp = row["timestamp"]
                if (type(timestamp) is not int or timestamp < 0
                        or timestamp + seconds > record["available_at"]
                        or row.get("is_authentic") is not True):
                    raise ValueError("Open, future, synthetic or invalid candle")
                if not all(Decimal(row[k]).is_finite() for k in ("open", "high", "low", "close", "volume")):
                    raise ValueError("Nonfinite candle value")
                timestamps.append(timestamp)
            if timestamps != sorted(set(timestamps)):
                raise ValueError("Unsorted or duplicate candles")
            frames[payload["symbol"]].add(resolution)
        records.append(record)
    captured_counts = {k.removeprefix("captured:"): v for k, v in manifest["counts"].items() if k.startswith("captured:")}
    if dict(counts) != captured_counts:
        raise ValueError("Captured record count mismatch")
    audit = {"run_id": manifest.get("run_id"), "code_sha": code,
             "trigger_sha": manifest.get("trigger_sha"), "manifest_status": manifest.get("status"),
             "scan_status": manifest.get("scan_status"), "compressed_bytes": len(data),
             "archive_sha256": hashlib.sha256(data).hexdigest(),
             "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
             "records": len(records), "kind_counts": dict(counts), "book_stage_counts": dict(stages),
             "markets_with_three_native_frames": sorted(s for s, values in frames.items() if values == set(INTERVALS)),
             "capture_counts_including_omissions": manifest["counts"],
             "unknown_checkout_provenance": code is None,
             "all_market_or_strategy_evidence_complete": False,
             "is_prediction_or_trade_or_pnl": False}
    return records, audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--run")
    parser.add_argument("--code")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _, result = read_capture(args.artifact, args.run, args.code)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    print(text)
