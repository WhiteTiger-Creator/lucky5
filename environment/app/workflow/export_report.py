#!/usr/bin/env python3
"""Compile certificate rotation drift windows into the responder report set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

SCHEMA_VERSION = "cert-rotation-v1"


def load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text())


def export_report(events: list[dict], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    severity_counts = {name: 0 for name in ("p1", "p2", "p3", "p4")}
    envs: set[str] = set()
    for event in events:
        severity = str(event.get("severity", ""))
        if severity in severity_counts:
            severity_counts[severity] += 1
        envs.add(str(event.get("env", "")))

    queue_rows = []
    for event in events:
        if str(event.get("severity")) == "p1":
            queue_rows.append(
                {
                    "ticket_id": str(event.get("alert_id", "")),
                    "env": event.get("env", ""),
                    "start_ms": int(event.get("start_ms", 0)),
                    "end_ms": int(event.get("seen_ms", 0)),
                    "priority": "high",
                }
            )

    queue_rows.sort(key=lambda row: row["start_ms"])

    summary = {
        "schema_version": SCHEMA_VERSION,
        "raw_alert_count": len(events),
        "unique_alert_ids": len({str(event.get("alert_id", "")) for event in events}),
        "canonical_alert_count": len(events),
        "env_count": len(envs),
        "severity_counts": severity_counts,
        "total_unmuted_duration_ms": 0,
        "total_freeze_overlap_ms": 0,
        "total_effective_duration_ms": 0,
        "longest_window_ms": 0,
        "queued_window_count": len(queue_rows),
        "muted_excluded_count": 0,
        "queue_hash_checksum": "",
    }

    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    (output_dir / "drift_windows.json").write_text(json.dumps({}, indent=2) + "\n")
    with (output_dir / "response_queue.jsonl").open("w", encoding="utf-8") as handle:
        for row in queue_rows:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/app/data/events.json")
    parser.add_argument("--output-dir", default="/app/output")
    args = parser.parse_args()

    events = load_json(Path(args.input))
    export_report(events, Path(args.output_dir))
    print(f"Wrote report to {args.output_dir}")


if __name__ == "__main__":
    main()
