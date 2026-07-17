#!/usr/bin/env python3
"""Reference fix for certificate-rotation drift chain compiler."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SCHEMA_VERSION = "cert-rotation-v1"
FREEZE_PATH = Path("/app/data/change_freezes.json")
REOPEN_PATH = Path("/app/data/reopen_windows.json")
ROTATION_PATH = Path("/app/data/rotation_windows.json")
DEFER_PATH = Path("/app/data/defer_windows.json")
CHAIN_TRUST_EDGES_PATH = Path("/app/data/chain_trust_edges.json")
SEVERITY_ORDER = ["p1", "p2", "p3", "p4"]
SEVERITY_RANK = {name: len(SEVERITY_ORDER) - idx for idx, name in enumerate(SEVERITY_ORDER)}
PRIORITY_ORDER = ["critical", "high", "medium"]
PRIORITY_RANK = {name: len(PRIORITY_ORDER) - idx for idx, name in enumerate(PRIORITY_ORDER)}
SUPPORTED_REOPEN_SCOPES = {"all", "p1", "p2"}
SUPPORTED_ROTATION_SCOPES = {"all", "p1", "p2"}
SUPPORTED_DEFER_SCOPES = {"all", "p1", "p2"}


def _normalize_severity(value: object) -> str:
    value = str(value).strip().lower()
    return value if value in SEVERITY_RANK else "p4"


def _normalize_env(value: object) -> str:
    value = str(value).strip().lower()
    return value if value else "unknown"


def _normalize_signature(value: object) -> str:
    return " ".join(str(value).split())


def _normalize_muted(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return bool(value)


def _normalize_ms(value: object) -> int:
    text = str(value).strip()
    try:
        return int(text)
    except (TypeError, ValueError):
        return 0


def _severity_rank(value: str) -> int:
    return SEVERITY_RANK.get(value, 0)


def _normalize_reopen_scope(value: object) -> str:
    scope = str(value).strip().lower()
    return scope if scope in SUPPORTED_REOPEN_SCOPES else ""


def _normalize_rotation_scope(value: object) -> str:
    scope = str(value).strip().lower()
    return scope if scope in SUPPORTED_ROTATION_SCOPES else ""


def _normalize_defer_scope(value: object) -> str:
    scope = str(value).strip().lower()
    return scope if scope in SUPPORTED_DEFER_SCOPES else ""


def _load_json(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _compact_intervals(intervals: list[tuple[int, int]]) -> list[tuple[int, int]]:
    merged: list[list[int]] = []
    for start, end in sorted(intervals):
        if not merged or start > merged[-1][1]:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return [(start, end) for start, end in merged]


def _window_overlaps(
    window_start: int, window_end: int, intervals: list[tuple[int, int]]
) -> list[tuple[int, int]]:
    segments: list[tuple[int, int]] = []
    for interval_start, interval_end in intervals:
        start = max(window_start, interval_start)
        end = min(window_end, interval_end)
        if end > start:
            segments.append((start, end))
    return segments


def _probe_overlap_ms(anchor_ms: int, intervals: list[tuple[int, int]], lookback_ms: int = 180) -> int:
    probe_start = anchor_ms - lookback_ms
    probe_end = anchor_ms + 1
    return sum(overlap_ms(probe_start, probe_end, start, end) for start, end in intervals)


def canonicalize_alerts(rows: list[dict]) -> list[dict]:
    deduped: dict[str, dict] = {}
    for row in rows:
        alert_id = str(row.get("alert_id", "")).strip()
        if not alert_id:
            continue
        candidate = {
            "alert_id": alert_id,
            "start_ms": _normalize_ms(row.get("start_ms", 0)),
            "end_ms": _normalize_ms(row.get("end_ms", 0)),
            "severity": _normalize_severity(row.get("severity", "")),
            "env": _normalize_env(row.get("env", "")),
            "signature": _normalize_signature(row.get("signature", "")),
            "muted": _normalize_muted(row.get("muted", False)),
        }
        existing = deduped.get(alert_id)
        if existing is None:
            deduped[alert_id] = candidate
            continue
        if candidate["end_ms"] > existing["end_ms"]:
            deduped[alert_id] = candidate
            continue
        if candidate["end_ms"] < existing["end_ms"]:
            continue
        if _severity_rank(candidate["severity"]) > _severity_rank(existing["severity"]):
            deduped[alert_id] = candidate
            continue
        if _severity_rank(candidate["severity"]) < _severity_rank(existing["severity"]):
            continue
        if len(candidate["signature"]) > len(existing["signature"]):
            deduped[alert_id] = candidate
            continue
        if len(candidate["signature"]) < len(existing["signature"]):
            continue
        if candidate["env"] > existing["env"]:
            deduped[alert_id] = candidate

    canonical = list(deduped.values())
    canonical.sort(key=lambda row: (row["env"], row["start_ms"], row["alert_id"]))
    return canonical


def overlap_ms(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
    return max(0, min(a_end, b_end) - max(a_start, b_start))


def freezes_by_env(freezes: list[dict]) -> dict[str, list[tuple[int, int]]]:
    by_env: dict[str, list[tuple[int, int]]] = {}
    for row in freezes:
        env = _normalize_env(row.get("env", ""))
        start = _normalize_ms(row.get("start_ms", 0))
        end = _normalize_ms(row.get("end_ms", 0))
        if end <= start:
            continue
        by_env.setdefault(env, []).append((start, end))
    return {env: _compact_intervals(by_env[env]) for env in by_env}


def reopen_by_scope(rows: list[dict]) -> dict[tuple[str, str], list[tuple[int, int]]]:
    by_key: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for row in rows:
        env = _normalize_env(row.get("env", ""))
        scope = _normalize_reopen_scope(row.get("severity_scope", ""))
        if not scope:
            continue
        start = _normalize_ms(row.get("start_ms", 0))
        end = _normalize_ms(row.get("end_ms", 0))
        if end <= start:
            continue
        by_key.setdefault((env, scope), []).append((start, end))
    return {key: _compact_intervals(intervals) for key, intervals in by_key.items()}


def rotation_by_scope(rows: list[dict]) -> dict[tuple[str, str], list[tuple[int, int]]]:
    by_key: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for row in rows:
        env = _normalize_env(row.get("env", ""))
        scope = _normalize_rotation_scope(row.get("severity_scope", ""))
        if not scope:
            continue
        start = _normalize_ms(row.get("start_ms", 0))
        end = _normalize_ms(row.get("end_ms", 0))
        if end <= start:
            continue
        by_key.setdefault((env, scope), []).append((start, end))
    return {key: _compact_intervals(intervals) for key, intervals in by_key.items()}


def defer_by_scope(rows: list[dict]) -> dict[tuple[str, str], list[tuple[int, int]]]:
    by_key: dict[tuple[str, str], list[tuple[int, int]]] = {}
    for row in rows:
        env = _normalize_env(row.get("env", ""))
        scope = _normalize_defer_scope(row.get("severity_scope", ""))
        if not scope:
            continue
        start = _normalize_ms(row.get("start_ms", 0))
        end = _normalize_ms(row.get("end_ms", 0))
        if end <= start:
            continue
        by_key.setdefault((env, scope), []).append((start, end))
    return {key: _compact_intervals(intervals) for key, intervals in by_key.items()}


def canonicalize_trust_edges(rows: list[dict]) -> dict[str, dict[str, int]]:
    edges: dict[str, dict[str, int]] = {}
    for row in rows:
        source = _normalize_env(row.get("source_env", ""))
        target = _normalize_env(row.get("target_env", ""))
        weight = _normalize_ms(row.get("weight", 0))
        if source == target or weight <= 0 or weight > 9:
            continue
        targets = edges.setdefault(source, {})
        targets[target] = max(targets.get(target, 0), weight)
    return {
        source: {target: targets[target] for target in sorted(targets)}
        for source, targets in sorted(edges.items())
    }


def _max_disjoint_trust_packing(
    origin: str,
    trust_edges: dict[str, dict[str, int]],
    hop_bound: int = 3,
) -> int:
    """Maximum total weight of node-disjoint simple paths out of `origin`.

    Enumerate every simple directed path of 1..hop_bound edges starting at
    `origin` (weight = sum of edge weights, node set = its non-origin nodes),
    then select a set of those paths sharing no non-origin node that maximises the
    summed weight. This is NOT the sum of each target's strongest path — paths
    that reuse a node cannot both be counted.
    """
    paths: list[tuple[int, frozenset[str]]] = []

    def enumerate_paths(node: str, weight: int, nodes: frozenset[str], depth: int) -> None:
        for target, edge_weight in trust_edges.get(node, {}).items():
            if target == origin or target in nodes:
                continue
            reached = nodes | {target}
            paths.append((weight + edge_weight, reached))
            if depth + 1 < hop_bound:
                enumerate_paths(target, weight + edge_weight, reached, depth + 1)

    enumerate_paths(origin, 0, frozenset(), 0)

    best_total = 0

    def pack(index: int, used: frozenset[str], total: int) -> None:
        nonlocal best_total
        if total > best_total:
            best_total = total
        if index >= len(paths):
            return
        pack(index + 1, used, total)
        weight, nodes = paths[index]
        if not (nodes & used):
            pack(index + 1, used | nodes, total + weight)

    pack(0, frozenset(), 0)
    return best_total


def strongest_trust_exposure(
    origin: str,
    trust_edges: dict[str, dict[str, int]],
) -> dict:
    best: dict[str, tuple[int, tuple[str, ...]]] = {}

    def visit(node: str, score: int, path: tuple[str, ...]) -> None:
        if len(path) - 1 >= 3:
            return
        for target, weight in trust_edges.get(node, {}).items():
            if target in path:
                continue
            next_path = path + (target,)
            next_score = score + weight
            current = best.get(target)
            if (
                current is None
                or next_score > current[0]
                or (next_score == current[0] and next_path < current[1])
            ):
                best[target] = (next_score, next_path)
            visit(target, next_score, next_path)

    visit(origin, 0, (origin,))
    reachable = sorted(best)
    exposure_score = _max_disjoint_trust_packing(origin, trust_edges, 3)
    strongest_path: tuple[str, ...] = (origin,)
    strongest_score = 0
    for target in reachable:
        score, path = best[target]
        if score > strongest_score or (
            score == strongest_score and path < strongest_path
        ):
            strongest_score = score
            strongest_path = path
    path_rows = [
        f"{target}:{best[target][0]}:{'>'.join(best[target][1])}"
        for target in reachable
    ]
    path_digest = hashlib.sha256(
        f"{origin}|{exposure_score}|{';'.join(path_rows)}".encode("utf-8")
    ).hexdigest()[:12]
    return {
        "trust_reachable_envs": reachable,
        "trust_exposure_score": exposure_score,
        "trust_strongest_path": list(strongest_path),
        "trust_path_digest": path_digest,
    }


def _scope_intervals_for_window(
    scope_map: dict[tuple[str, str], list[tuple[int, int]]],
    env: str,
    max_severity: str,
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    all_intervals = scope_map.get((env, "all"), [])
    severity_intervals = scope_map.get((env, max_severity), [])
    # p2 windows borrow p1 scope when p2 is absent.
    if max_severity == "p2" and not severity_intervals:
        severity_intervals = scope_map.get((env, "p1"), [])
    return all_intervals, severity_intervals


def build_drift_windows(
    canonical: list[dict],
    freeze_rows: list[dict],
    reopen_rows: list[dict],
    rotation_rows: list[dict],
    defer_rows: list[dict],
    trust_edge_rows: list[dict],
) -> tuple[
    dict[str, list[dict]],
    dict[str, list[tuple[int, int]]],
    dict[tuple[str, str], list[tuple[int, int]]],
    dict[tuple[str, str], list[tuple[int, int]]],
    dict[tuple[str, str], list[tuple[int, int]]],
    dict[str, dict[str, int]],
]:
    grouped: dict[str, list[dict]] = {}
    for row in canonical:
        if row["muted"]:
            continue
        grouped.setdefault(row["env"], []).append(row)

    freeze_map = freezes_by_env(freeze_rows)
    reopen_map = reopen_by_scope(reopen_rows)
    rotation_map = rotation_by_scope(rotation_rows)
    defer_map = defer_by_scope(defer_rows)
    trust_edges = canonicalize_trust_edges(trust_edge_rows)
    result: dict[str, list[dict]] = {}
    for env, alerts in grouped.items():
        alerts.sort(key=lambda row: (row["start_ms"], row["end_ms"], row["alert_id"]))
        windows: list[dict] = []
        current: dict | None = None
        for row in alerts:
            if current is None:
                current = {
                    "start_ms": row["start_ms"],
                    "end_ms": row["end_ms"],
                    "source_alert_ids": [row["alert_id"]],
                    "max_severity": row["severity"],
                }
                continue
            if row["start_ms"] <= current["end_ms"] + 45:
                current["end_ms"] = max(current["end_ms"], row["end_ms"])
                current["source_alert_ids"].append(row["alert_id"])
                if _severity_rank(row["severity"]) > _severity_rank(current["max_severity"]):
                    current["max_severity"] = row["severity"]
            else:
                windows.append(current)
                current = {
                    "start_ms": row["start_ms"],
                    "end_ms": row["end_ms"],
                    "source_alert_ids": [row["alert_id"]],
                    "max_severity": row["severity"],
                }
        if current is not None:
            windows.append(current)

        normalized_windows: list[dict] = []
        for window in windows:
            duration = max(window["end_ms"] - window["start_ms"], 0)
            freeze_segments = _window_overlaps(
                window["start_ms"], window["end_ms"], freeze_map.get(env, [])
            )
            freeze_overlap = sum(end - start for start, end in freeze_segments)
            effective_duration = max(duration - freeze_overlap, 0)

            reopen_all, reopen_severity = _scope_intervals_for_window(
                reopen_map, env, window["max_severity"]
            )
            reopen_segments = _window_overlaps(
                window["start_ms"], window["end_ms"], reopen_all
            )
            reopen_segments.extend(
                _window_overlaps(
                    window["start_ms"],
                    window["end_ms"],
                    reopen_severity,
                )
            )
            compacted_reopen_segments = _compact_intervals(reopen_segments)
            reopen_overlap = sum(end - start for start, end in compacted_reopen_segments)
            risk_adjusted_duration = max(effective_duration - (reopen_overlap // 2), 0)

            rotation_all, rotation_severity = _scope_intervals_for_window(
                rotation_map, env, window["max_severity"]
            )
            rotation_segments = _window_overlaps(
                window["start_ms"], window["end_ms"], rotation_all
            )
            rotation_segments.extend(
                _window_overlaps(
                    window["start_ms"],
                    window["end_ms"],
                    rotation_severity,
                )
            )
            compacted_rotation_segments = _compact_intervals(rotation_segments)
            rotation_overlap = sum(end - start for start, end in compacted_rotation_segments)
            dispatchable_duration = max(risk_adjusted_duration - (rotation_overlap // 3), 0)
            defer_all, defer_severity = _scope_intervals_for_window(
                defer_map, env, window["max_severity"]
            )
            defer_segments = _window_overlaps(
                window["start_ms"], window["end_ms"], defer_all
            )
            defer_segments.extend(
                _window_overlaps(
                    window["start_ms"],
                    window["end_ms"],
                    defer_severity,
                )
            )
            compacted_defer_segments = _compact_intervals(defer_segments)
            defer_overlap = sum(end - start for start, end in compacted_defer_segments)
            actionable_duration = max(dispatchable_duration - (defer_overlap // 4), 0)
            normalized_windows.append(
                {
                    "start_ms": window["start_ms"],
                    "end_ms": window["end_ms"],
                    "duration_ms": duration,
                    "freeze_overlap_ms": freeze_overlap,
                    "freeze_segment_count": len(freeze_segments),
                    "effective_duration_ms": effective_duration,
                    "reopen_overlap_ms": reopen_overlap,
                    "reopen_segment_count": len(compacted_reopen_segments),
                    "risk_adjusted_duration_ms": risk_adjusted_duration,
                    "rotation_overlap_ms": rotation_overlap,
                    "rotation_segment_count": len(compacted_rotation_segments),
                    "dispatchable_duration_ms": dispatchable_duration,
                    "defer_overlap_ms": defer_overlap,
                    "defer_segment_count": len(compacted_defer_segments),
                    "actionable_duration_ms": actionable_duration,
                    "alert_count": len(window["source_alert_ids"]),
                    "source_alert_ids": sorted(window["source_alert_ids"]),
                    "max_severity": window["max_severity"],
                }
            )
        normalized_windows.sort(key=lambda row: row["start_ms"])
        previous_end_ms: int | None = None
        previous_carry_out_ms = 0
        trust_exposure = strongest_trust_exposure(env, trust_edges)
        for window in normalized_windows:
            idle_gap_ms = (
                0
                if previous_end_ms is None
                else max(window["start_ms"] - previous_end_ms, 0)
            )
            carry_in_ms = max(previous_carry_out_ms - (idle_gap_ms // 2), 0)
            ledger_adjusted_actionable_ms = (
                window["actionable_duration_ms"] + (carry_in_ms // 4)
            )
            carry_out_ms = min(
                carry_in_ms
                + window["actionable_duration_ms"]
                + (window["freeze_segment_count"] * 8)
                + (window["rotation_segment_count"] * 15)
                + (window["defer_segment_count"] * 10),
                2000,
            )
            window["idle_gap_ms"] = idle_gap_ms
            window["carry_in_ms"] = carry_in_ms
            window["carry_out_ms"] = carry_out_ms
            window["ledger_adjusted_actionable_ms"] = ledger_adjusted_actionable_ms
            window.update(trust_exposure)
            previous_end_ms = window["end_ms"]
            previous_carry_out_ms = carry_out_ms
        result[env] = normalized_windows

    return (
        {env: result[env] for env in sorted(result)},
        freeze_map,
        reopen_map,
        rotation_map,
        defer_map,
        trust_edges,
    )


def build_response_queue(
    drift_windows: dict[str, list[dict]],
    reopen_map: dict[tuple[str, str], list[tuple[int, int]]],
    rotation_map: dict[tuple[str, str], list[tuple[int, int]]],
    defer_map: dict[tuple[str, str], list[tuple[int, int]]],
) -> list[dict]:
    queue: list[dict] = []
    for env, windows in drift_windows.items():
        for window in windows:
            if window["max_severity"] not in {"p1", "p2"}:
                continue
            include_min_ms = 180 if window["max_severity"] == "p1" else 225
            if window["ledger_adjusted_actionable_ms"] < include_min_ms:
                continue

            reopen_all, reopen_severity = _scope_intervals_for_window(
                reopen_map, env, window["max_severity"]
            )
            all_probe_ms = _probe_overlap_ms(window["end_ms"], reopen_all)
            severity_probe_ms = _probe_overlap_ms(
                window["end_ms"],
                reopen_severity,
            )
            stability_pressure_score = (
                (all_probe_ms // 30)
                + (severity_probe_ms // 20)
                + max(window["alert_count"] - 1, 0)
            )
            rotation_all, rotation_severity = _scope_intervals_for_window(
                rotation_map, env, window["max_severity"]
            )
            all_rotation_probe_ms = _probe_overlap_ms(
                window["end_ms"], rotation_all, lookback_ms=240
            )
            severity_rotation_probe_ms = _probe_overlap_ms(
                window["end_ms"],
                rotation_severity,
                lookback_ms=240,
            )
            volatility_index = (
                stability_pressure_score
                + (all_rotation_probe_ms // 24)
                + (severity_rotation_probe_ms // 16)
                + (window["rotation_segment_count"] * 2)
            )
            defer_all, defer_severity = _scope_intervals_for_window(
                defer_map, env, window["max_severity"]
            )
            all_defer_probe_ms = _probe_overlap_ms(
                window["end_ms"], defer_all, lookback_ms=300
            )
            severity_defer_probe_ms = _probe_overlap_ms(
                window["end_ms"],
                defer_severity,
                lookback_ms=300,
            )
            defer_pressure_score = (
                (all_defer_probe_ms // 40)
                + (severity_defer_probe_ms // 28)
                + window["defer_segment_count"]
            )
            ledger_pressure_score = (
                (window["carry_out_ms"] // 60)
                + (window["carry_in_ms"] // 120)
                + max(window["alert_count"] - 1, 0)
            )
            stability_index = (
                volatility_index
                + defer_pressure_score
                + ledger_pressure_score
                + min(window["trust_exposure_score"] // 2, 12)
            )
            if (
                window["max_severity"] == "p1"
                and window["ledger_adjusted_actionable_ms"] >= 235
            ) or (
                window["ledger_adjusted_actionable_ms"] >= 500
                or stability_index >= 20
                or window["trust_exposure_score"] >= 24
            ):
                priority = "critical"
            elif window["ledger_adjusted_actionable_ms"] >= 265 or (
                window["alert_count"] >= 3 and window["max_severity"] in {"p1", "p2"}
            ) or (
                window["rotation_segment_count"] == 0
                and window["risk_adjusted_duration_ms"] >= 340
            ) or (
                defer_pressure_score > 0 and window["dispatchable_duration_ms"] >= 320
            ) or (
                window["reopen_segment_count"] == 0 and window["duration_ms"] >= 420
            ) or (
                window["trust_exposure_score"] >= 12
            ):
                priority = "high"
            else:
                priority = "medium"

            queue_hash = hashlib.sha1(
                (
                    f"{env}|{window['start_ms']}|{window['end_ms']}|"
                    f"{','.join(window['source_alert_ids'])}|{window['max_severity']}|"
                    f"{window['freeze_segment_count']}|{window['reopen_segment_count']}|"
                    f"{window['rotation_segment_count']}|{window['defer_segment_count']}|"
                    f"{window['carry_in_ms']}|{window['carry_out_ms']}|"
                    f"{window['ledger_adjusted_actionable_ms']}|{stability_pressure_score}|"
                    f"{volatility_index}|{defer_pressure_score}|{ledger_pressure_score}|"
                    f"{window['trust_exposure_score']}|{window['trust_path_digest']}"
                ).encode("utf-8")
            ).hexdigest()[:12]
            ticket_id = f"{env}:{window['start_ms']}-{window['end_ms']}"
            window_digest = hashlib.sha1(
                (
                    f"{ticket_id}|{priority}|{window['actionable_duration_ms']}|"
                    f"{window['ledger_adjusted_actionable_ms']}|{stability_index}|"
                    f"{defer_pressure_score}|{volatility_index}|{ledger_pressure_score}|"
                    f"{window['trust_exposure_score']}|{window['trust_path_digest']}"
                ).encode("utf-8")
            ).hexdigest()[:10]

            queue.append(
                {
                    "ticket_id": ticket_id,
                    "env": env,
                    "start_ms": window["start_ms"],
                    "end_ms": window["end_ms"],
                    "duration_ms": window["duration_ms"],
                    "freeze_overlap_ms": window["freeze_overlap_ms"],
                    "freeze_segment_count": window["freeze_segment_count"],
                    "effective_duration_ms": window["effective_duration_ms"],
                    "reopen_overlap_ms": window["reopen_overlap_ms"],
                    "reopen_segment_count": window["reopen_segment_count"],
                    "risk_adjusted_duration_ms": window["risk_adjusted_duration_ms"],
                    "rotation_overlap_ms": window["rotation_overlap_ms"],
                    "rotation_segment_count": window["rotation_segment_count"],
                    "dispatchable_duration_ms": window["dispatchable_duration_ms"],
                    "defer_overlap_ms": window["defer_overlap_ms"],
                    "defer_segment_count": window["defer_segment_count"],
                    "actionable_duration_ms": window["actionable_duration_ms"],
                    "idle_gap_ms": window["idle_gap_ms"],
                    "carry_in_ms": window["carry_in_ms"],
                    "carry_out_ms": window["carry_out_ms"],
                    "ledger_adjusted_actionable_ms": window[
                        "ledger_adjusted_actionable_ms"
                    ],
                    "trust_reachable_envs": list(window["trust_reachable_envs"]),
                    "trust_exposure_score": window["trust_exposure_score"],
                    "trust_strongest_path": list(window["trust_strongest_path"]),
                    "trust_path_digest": window["trust_path_digest"],
                    "alert_count": window["alert_count"],
                    "source_alert_ids": list(window["source_alert_ids"]),
                    "max_severity": window["max_severity"],
                    "stability_pressure_score": stability_pressure_score,
                    "volatility_index": volatility_index,
                    "defer_pressure_score": defer_pressure_score,
                    "ledger_pressure_score": ledger_pressure_score,
                    "stability_index": stability_index,
                    "priority": priority,
                    "queue_hash": queue_hash,
                    "window_digest": window_digest,
                }
            )

    queue.sort(
        key=lambda row: (
            -PRIORITY_RANK[row["priority"]],
            -row["ledger_adjusted_actionable_ms"],
            -row["actionable_duration_ms"],
            -row["stability_index"],
            -row["trust_exposure_score"],
            -row["defer_pressure_score"],
            -row["volatility_index"],
            -row["dispatchable_duration_ms"],
            -row["risk_adjusted_duration_ms"],
            -row["freeze_segment_count"],
            -row["alert_count"],
            row["env"],
            row["start_ms"],
        )
    )
    return queue


def build_summary(
    raw_rows: list[dict],
    canonical: list[dict],
    freeze_map: dict[str, list[tuple[int, int]]],
    reopen_map: dict[tuple[str, str], list[tuple[int, int]]],
    rotation_map: dict[tuple[str, str], list[tuple[int, int]]],
    defer_map: dict[tuple[str, str], list[tuple[int, int]]],
    trust_edges: dict[str, dict[str, int]],
    drift_windows: dict[str, list[dict]],
    queue: list[dict],
) -> dict:
    severity_counts = {name: 0 for name in SEVERITY_ORDER}
    for row in canonical:
        severity_counts[_normalize_severity(row["severity"])] += 1

    total_unmuted_duration = 0
    total_overlap = 0
    total_segments = 0
    total_effective = 0
    total_reopen_overlap = 0
    total_reopen_segments = 0
    total_risk_adjusted = 0
    total_rotation_overlap = 0
    total_rotation_segments = 0
    total_dispatchable = 0
    total_defer_overlap = 0
    total_defer_segments = 0
    total_actionable = 0
    total_ledger_adjusted_actionable = 0
    longest_window = 0
    for windows in drift_windows.values():
        for window in windows:
            total_unmuted_duration += window["duration_ms"]
            total_overlap += window["freeze_overlap_ms"]
            total_segments += window["freeze_segment_count"]
            total_effective += window["effective_duration_ms"]
            total_reopen_overlap += window["reopen_overlap_ms"]
            total_reopen_segments += window["reopen_segment_count"]
            total_risk_adjusted += window["risk_adjusted_duration_ms"]
            total_rotation_overlap += window["rotation_overlap_ms"]
            total_rotation_segments += window["rotation_segment_count"]
            total_dispatchable += window["dispatchable_duration_ms"]
            total_defer_overlap += window["defer_overlap_ms"]
            total_defer_segments += window["defer_segment_count"]
            total_actionable += window["actionable_duration_ms"]
            total_ledger_adjusted_actionable += window[
                "ledger_adjusted_actionable_ms"
            ]
            longest_window = max(longest_window, window["duration_ms"])

    muted_excluded_count = sum(1 for row in canonical if row["muted"])
    canonical_alert_checksum = hashlib.sha256(
        "\n".join(
            (
                f"{row['alert_id']}|{row['env']}|{row['start_ms']}|{row['end_ms']}|"
                f"{row['severity']}|{1 if row['muted'] else 0}|{row['signature']}"
            )
            for row in canonical
        ).encode("utf-8")
    ).hexdigest()
    freeze_compaction_checksum = hashlib.sha256(
        "\n".join(
            f"{env}|{start}|{end}"
            for env in sorted(freeze_map)
            for start, end in freeze_map[env]
        ).encode("utf-8")
    ).hexdigest()
    reopen_compaction_checksum = hashlib.sha256(
        "\n".join(
            f"{env}|{scope}|{start}|{end}"
            for env, scope in sorted(reopen_map)
            for start, end in reopen_map[(env, scope)]
        ).encode("utf-8")
    ).hexdigest()
    rotation_compaction_checksum = hashlib.sha256(
        "\n".join(
            f"{env}|{scope}|{start}|{end}"
            for env, scope in sorted(rotation_map)
            for start, end in rotation_map[(env, scope)]
        ).encode("utf-8")
    ).hexdigest()
    defer_compaction_checksum = hashlib.sha256(
        "\n".join(
            f"{env}|{scope}|{start}|{end}"
            for env, scope in sorted(defer_map)
            for start, end in defer_map[(env, scope)]
        ).encode("utf-8")
    ).hexdigest()
    trust_edge_checksum = hashlib.sha256(
        "\n".join(
            f"{source}|{target}|{trust_edges[source][target]}"
            for source in sorted(trust_edges)
            for target in sorted(trust_edges[source])
        ).encode("utf-8")
    ).hexdigest()
    queue_checksum = hashlib.sha256(
        "|".join(row["queue_hash"] for row in queue).encode("utf-8")
    ).hexdigest()
    window_digest_checksum = hashlib.sha256(
        "|".join(row["window_digest"] for row in queue).encode("utf-8")
    ).hexdigest()
    ledger_checksum = hashlib.sha256(
        "\n".join(
            (
                f"{env}|{window['start_ms']}|{window['idle_gap_ms']}|"
                f"{window['carry_in_ms']}|{window['carry_out_ms']}|"
                f"{window['ledger_adjusted_actionable_ms']}"
            )
            for env in sorted(drift_windows)
            for window in drift_windows[env]
        ).encode("utf-8")
    ).hexdigest()
    trust_path_digest_checksum = hashlib.sha256(
        "|".join(row["trust_path_digest"] for row in queue).encode("utf-8")
    ).hexdigest()

    return {
        "schema_version": SCHEMA_VERSION,
        "raw_alert_count": len(raw_rows),
        "unique_alert_ids": len(
            {str(row.get("alert_id", "")).strip() for row in raw_rows if str(row.get("alert_id", "")).strip()}
        ),
        "canonical_alert_count": len(canonical),
        "env_count": len(drift_windows),
        "severity_counts": severity_counts,
        "total_unmuted_duration_ms": total_unmuted_duration,
        "total_freeze_overlap_ms": total_overlap,
        "total_freeze_segment_count": total_segments,
        "total_effective_duration_ms": total_effective,
        "total_reopen_overlap_ms": total_reopen_overlap,
        "total_reopen_segment_count": total_reopen_segments,
        "total_risk_adjusted_duration_ms": total_risk_adjusted,
        "total_rotation_overlap_ms": total_rotation_overlap,
        "total_rotation_segment_count": total_rotation_segments,
        "total_dispatchable_duration_ms": total_dispatchable,
        "total_defer_overlap_ms": total_defer_overlap,
        "total_defer_segment_count": total_defer_segments,
        "total_actionable_duration_ms": total_actionable,
        "total_ledger_adjusted_actionable_ms": total_ledger_adjusted_actionable,
        "longest_window_ms": longest_window,
        "queued_window_count": len(queue),
        "muted_excluded_count": muted_excluded_count,
        "max_stability_pressure_score": max(
            (row["stability_pressure_score"] for row in queue),
            default=0,
        ),
        "max_volatility_index": max((row["volatility_index"] for row in queue), default=0),
        "max_defer_pressure_score": max((row["defer_pressure_score"] for row in queue), default=0),
        "max_ledger_pressure_score": max(
            (row["ledger_pressure_score"] for row in queue),
            default=0,
        ),
        "max_trust_exposure_score": max(
            (row["trust_exposure_score"] for row in queue),
            default=0,
        ),
        "max_carry_out_ms": max(
            (
                window["carry_out_ms"]
                for windows in drift_windows.values()
                for window in windows
            ),
            default=0,
        ),
        "max_stability_index": max((row["stability_index"] for row in queue), default=0),
        "canonical_alert_checksum": canonical_alert_checksum,
        "queue_hash_checksum": queue_checksum,
        "freeze_compaction_checksum": freeze_compaction_checksum,
        "reopen_compaction_checksum": reopen_compaction_checksum,
        "rotation_compaction_checksum": rotation_compaction_checksum,
        "defer_compaction_checksum": defer_compaction_checksum,
        "trust_edge_checksum": trust_edge_checksum,
        "window_digest_checksum": window_digest_checksum,
        "ledger_checksum": ledger_checksum,
        "trust_path_digest_checksum": trust_path_digest_checksum,
    }


def export_report(
    events: list[dict],
    output_dir: Path,
    freeze_rows: list[dict],
    reopen_rows: list[dict],
    rotation_rows: list[dict],
    defer_rows: list[dict],
    trust_edge_rows: list[dict],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    canonical = canonicalize_alerts(events)
    (
        drift_windows,
        freeze_map,
        reopen_map,
        rotation_map,
        defer_map,
        trust_edges,
    ) = build_drift_windows(
        canonical,
        freeze_rows,
        reopen_rows,
        rotation_rows,
        defer_rows,
        trust_edge_rows,
    )
    queue = build_response_queue(drift_windows, reopen_map, rotation_map, defer_map)
    summary = build_summary(
        events,
        canonical,
        freeze_map,
        reopen_map,
        rotation_map,
        defer_map,
        trust_edges,
        drift_windows,
        queue,
    )

    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (output_dir / "drift_windows.json").write_text(
        json.dumps(drift_windows, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "response_queue.jsonl").open("w", encoding="utf-8") as handle:
        for row in queue:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="/app/data/events.json")
    parser.add_argument("--output-dir", default="/app/output")
    args = parser.parse_args()

    events = _load_json(Path(args.input))
    freeze_rows = _load_json(FREEZE_PATH)
    reopen_rows = _load_json(REOPEN_PATH)
    rotation_rows = _load_json(ROTATION_PATH)
    defer_rows = _load_json(DEFER_PATH)
    trust_edge_rows = _load_json(CHAIN_TRUST_EDGES_PATH)
    export_report(
        events,
        Path(args.output_dir),
        freeze_rows,
        reopen_rows,
        rotation_rows,
        defer_rows,
        trust_edge_rows,
    )
    print(f"Wrote report to {args.output_dir}")


if __name__ == "__main__":
    main()
