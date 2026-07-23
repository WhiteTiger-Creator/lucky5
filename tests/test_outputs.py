"""Verifier tests for certificate-rotation drift compiler task."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

WORKFLOW_PATH = Path("/app/workflow/export_report.py")
ORIGINAL_WORKFLOW_PATH = Path("/app/workflow/.export_report.original")
DEFAULT_INPUT = Path("/app/data/events.json")
FREEZE_PATH = Path("/app/data/change_freezes.json")
REOPEN_PATH = Path("/app/data/reopen_windows.json")
ROTATION_PATH = Path("/app/data/rotation_windows.json")
DEFER_PATH = Path("/app/data/defer_windows.json")
TRUST_EDGES_PATH = Path("/app/data/chain_trust_edges.json")
SPEC_PATH = Path("/app/docs/report_spec.json")
EXPECTED_FIXTURE = Path("/tests/fixtures/expected_summary.json")
ALT_INPUT = Path("/tests/fixtures/alt_events.json")

SEVERITY_ORDER = ["p1", "p2", "p3", "p4"]
PRIORITY_ORDER = ["critical", "high", "medium"]
PRIORITY_RANK = {name: len(PRIORITY_ORDER) - idx for idx, name in enumerate(PRIORITY_ORDER)}

FIXTURE = json.loads(EXPECTED_FIXTURE.read_text())
SPEC = json.loads(SPEC_PATH.read_text())


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _load_jsonl(path: Path):
    rows = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if raw:
            rows.append(json.loads(raw))
    return rows


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def test_checksum_serialization_contract_vectors():
    """The checksum serialization rules reproduce the contract's worked test vectors byte for byte."""
    vectors = SPEC["summary_json"]["checksum_test_vectors"]
    for prefix in ("canonical_alert", "freeze", "scoped", "ledger", "trust_edge"):
        payload = vectors[f"{prefix}_payload"].encode("utf-8")
        assert hashlib.sha256(payload).hexdigest() == vectors[f"{prefix}_sha256"]


def _run_pipeline(tmp_path: Path, script_path: Path = WORKFLOW_PATH, input_path: Path = DEFAULT_INPUT):
    out_dir = tmp_path / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, str(script_path), "--input", str(input_path), "--output-dir", str(out_dir)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    summary = _load_json(out_dir / "summary.json")
    windows = _load_json(out_dir / "drift_windows.json")
    queue = _load_jsonl(out_dir / "response_queue.jsonl")
    return out_dir, summary, windows, queue


@pytest.fixture(scope="session")
def primary_outputs(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("primary")
    return _run_pipeline(tmp)


def test_cli_exists():
    """The reconciler exists at its operational path and runs successfully."""
    assert WORKFLOW_PATH.exists()


def test_output_dir_contains_exactly_three_files(primary_outputs):
    """A run writes exactly the three contracted output files and nothing else."""
    out_dir, _, _, _ = primary_outputs
    names = sorted(path.name for path in out_dir.iterdir() if path.is_file())
    assert names == ["drift_windows.json", "response_queue.jsonl", "summary.json"]


def test_primary_summary_matches_fixture(primary_outputs):
    """summary.json from the primary event stream matches the expected fixture exactly."""
    _, summary, _, _ = primary_outputs
    assert summary == FIXTURE["primary"]["summary"]


def test_primary_windows_matches_fixture(primary_outputs):
    """drift_windows.json from the primary event stream matches the expected fixture exactly."""
    _, _, windows, _ = primary_outputs
    assert windows == FIXTURE["primary"]["windows"]


def test_primary_queue_matches_fixture(primary_outputs):
    """response_queue.jsonl from the primary event stream matches the expected fixture exactly."""
    _, _, _, queue = primary_outputs
    assert queue == FIXTURE["primary"]["queue_rows"]


def test_summary_schema(primary_outputs):
    """summary.json carries exactly the contracted key set with correctly typed values."""
    _, summary, _, _ = primary_outputs
    assert set(summary) == {
        "schema_version",
        "raw_alert_count",
        "unique_alert_ids",
        "canonical_alert_count",
        "env_count",
        "severity_counts",
        "total_unmuted_duration_ms",
        "total_freeze_overlap_ms",
        "total_freeze_segment_count",
        "total_effective_duration_ms",
        "total_reopen_overlap_ms",
        "total_reopen_segment_count",
        "total_risk_adjusted_duration_ms",
        "total_rotation_overlap_ms",
        "total_rotation_segment_count",
        "total_dispatchable_duration_ms",
        "total_defer_overlap_ms",
        "total_defer_segment_count",
        "total_actionable_duration_ms",
        "total_ledger_adjusted_actionable_ms",
        "longest_window_ms",
        "queued_window_count",
        "muted_excluded_count",
        "max_stability_pressure_score",
        "max_volatility_index",
        "max_defer_pressure_score",
        "max_ledger_pressure_score",
        "max_trust_exposure_score",
        "max_carry_out_ms",
        "max_stability_index",
        "canonical_alert_checksum",
        "queue_hash_checksum",
        "freeze_compaction_checksum",
        "reopen_compaction_checksum",
        "rotation_compaction_checksum",
        "defer_compaction_checksum",
        "trust_edge_checksum",
        "window_digest_checksum",
        "ledger_checksum",
        "trust_path_digest_checksum",
    }
    assert summary["schema_version"] == "cert-rotation-v1"
    assert list(summary["severity_counts"]) == SEVERITY_ORDER
    assert len(summary["canonical_alert_checksum"]) == 64
    assert len(summary["queue_hash_checksum"]) == 64
    assert len(summary["freeze_compaction_checksum"]) == 64
    assert len(summary["reopen_compaction_checksum"]) == 64
    assert len(summary["rotation_compaction_checksum"]) == 64
    assert len(summary["defer_compaction_checksum"]) == 64
    assert len(summary["trust_edge_checksum"]) == 64
    assert len(summary["window_digest_checksum"]) == 64
    assert len(summary["ledger_checksum"]) == 64
    assert len(summary["trust_path_digest_checksum"]) == 64


def test_windows_schema_and_sorting(primary_outputs):
    """drift_windows.json rows carry the contracted keys and are sorted as the contract specifies."""
    _, _, windows, _ = primary_outputs
    expected_keys = {
        "start_ms",
        "end_ms",
        "duration_ms",
        "freeze_overlap_ms",
        "freeze_segment_count",
        "effective_duration_ms",
        "reopen_overlap_ms",
        "reopen_segment_count",
        "risk_adjusted_duration_ms",
        "rotation_overlap_ms",
        "rotation_segment_count",
        "dispatchable_duration_ms",
        "defer_overlap_ms",
        "defer_segment_count",
        "actionable_duration_ms",
        "idle_gap_ms",
        "carry_in_ms",
        "carry_out_ms",
        "ledger_adjusted_actionable_ms",
        "trust_reachable_envs",
        "trust_exposure_score",
        "trust_strongest_path",
        "trust_path_digest",
        "alert_count",
        "source_alert_ids",
        "max_severity",
    }
    assert list(windows) == sorted(windows)
    for env_windows in windows.values():
        starts = [row["start_ms"] for row in env_windows]
        assert starts == sorted(starts)
        for row in env_windows:
            assert set(row) == expected_keys
            assert row["duration_ms"] == max(row["end_ms"] - row["start_ms"], 0)
            assert row["effective_duration_ms"] == max(
                row["duration_ms"] - row["freeze_overlap_ms"], 0
            )
            assert row["risk_adjusted_duration_ms"] == max(
                row["effective_duration_ms"] - (-(-row["reopen_overlap_ms"] // 2)), 0
            )
            # Under #CR-5382 the rotation subtraction uses the overlap MINUS the span
            # shared with reopen, while rotation_overlap_ms reports the unadjusted
            # value. So the reported overlap bounds the subtraction rather than
            # fixing it: the exact values are pinned by the fixture comparison.
            assert row["dispatchable_duration_ms"] >= max(
                row["risk_adjusted_duration_ms"] - (-(-row["rotation_overlap_ms"] // 3)), 0
            )
            assert row["dispatchable_duration_ms"] <= row["risk_adjusted_duration_ms"]
            assert row["actionable_duration_ms"] == max(
                row["dispatchable_duration_ms"] - (-(-row["defer_overlap_ms"] // 4)), 0
            )
            assert row["ledger_adjusted_actionable_ms"] == (
                row["actionable_duration_ms"] + (-(-row["carry_in_ms"] // 4))
            )
            assert row["source_alert_ids"] == sorted(row["source_alert_ids"])
            assert row["trust_reachable_envs"] == sorted(row["trust_reachable_envs"])
            assert len(row["trust_path_digest"]) == 12


def test_queue_required_fields(primary_outputs):
    """Every queue row carries the required fields with contract-conformant shapes."""
    _, _, _, queue = primary_outputs
    expected_keys = {
        "ticket_id",
        "env",
        "start_ms",
        "end_ms",
        "duration_ms",
        "freeze_overlap_ms",
        "freeze_segment_count",
        "effective_duration_ms",
        "reopen_overlap_ms",
        "reopen_segment_count",
        "risk_adjusted_duration_ms",
        "rotation_overlap_ms",
        "rotation_segment_count",
        "dispatchable_duration_ms",
        "defer_overlap_ms",
        "defer_segment_count",
        "actionable_duration_ms",
        "idle_gap_ms",
        "carry_in_ms",
        "carry_out_ms",
        "ledger_adjusted_actionable_ms",
        "trust_reachable_envs",
        "trust_exposure_score",
        "trust_strongest_path",
        "trust_path_digest",
        "alert_count",
        "source_alert_ids",
        "max_severity",
        "stability_pressure_score",
        "volatility_index",
        "defer_pressure_score",
        "ledger_pressure_score",
        "stability_index",
        "priority",
        "queue_hash",
        "window_digest",
    }
    for row in queue:
        assert set(row) == expected_keys
        assert row["priority"] in PRIORITY_RANK
        assert len(row["queue_hash"]) == 12
        assert len(row["window_digest"]) == 10


def test_priority_rules(primary_outputs):
    """Queue rows receive priority tiers according to the decided admission thresholds."""
    _, _, _, queue = primary_outputs
    for row in queue:
        if (
            row["max_severity"] == "p1"
            and row["ledger_adjusted_actionable_ms"] >= 454
        ) or (
            row["ledger_adjusted_actionable_ms"] >= 551
            or row["stability_index"] >= 29
            or row["trust_exposure_score"] >= 35
        ):
            assert row["priority"] == "critical"
        elif row["ledger_adjusted_actionable_ms"] >= 400 or (
            row["alert_count"] >= 2 and row["max_severity"] in {"p1", "p2"}
        ) or (
            row["rotation_segment_count"] == 0
            and row["risk_adjusted_duration_ms"] >= 340
        ) or (
            row["defer_pressure_score"] > 0
            and row["dispatchable_duration_ms"] >= 320
        ) or (
            row["reopen_segment_count"] == 0 and row["duration_ms"] >= 420
        ) or row["trust_exposure_score"] >= 12:
            assert row["priority"] == "high"
        else:
            assert row["priority"] == "medium"


def test_queue_sorted(primary_outputs):
    """The responder queue is emitted in the fully specified tie-broken order."""
    _, _, _, queue = primary_outputs
    assert queue == sorted(
        queue,
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
        ),
    )


def test_response_queue_jsonl_compact(primary_outputs):
    """response_queue.jsonl rows are serialized as compact JSON with no padding whitespace."""
    out_dir, _, _, _ = primary_outputs
    for line in (out_dir / "response_queue.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        assert ": " not in line
        parsed = json.loads(line)
        assert json.dumps(parsed, separators=(",", ":")) == line


def test_summary_math_consistency(primary_outputs):
    """Aggregate summary fields are internally consistent with the emitted windows and queue."""
    _, summary, windows, _ = primary_outputs
    duration_total = 0
    overlap_total = 0
    segment_total = 0
    effective_total = 0
    reopen_overlap_total = 0
    reopen_segment_total = 0
    risk_adjusted_total = 0
    rotation_overlap_total = 0
    rotation_segment_total = 0
    dispatchable_total = 0
    defer_overlap_total = 0
    defer_segment_total = 0
    actionable_total = 0
    ledger_adjusted_total = 0
    longest = 0
    for env_windows in windows.values():
        for row in env_windows:
            duration_total += row["duration_ms"]
            overlap_total += row["freeze_overlap_ms"]
            segment_total += row["freeze_segment_count"]
            effective_total += row["effective_duration_ms"]
            reopen_overlap_total += row["reopen_overlap_ms"]
            reopen_segment_total += row["reopen_segment_count"]
            risk_adjusted_total += row["risk_adjusted_duration_ms"]
            rotation_overlap_total += row["rotation_overlap_ms"]
            rotation_segment_total += row["rotation_segment_count"]
            dispatchable_total += row["dispatchable_duration_ms"]
            defer_overlap_total += row["defer_overlap_ms"]
            defer_segment_total += row["defer_segment_count"]
            actionable_total += row["actionable_duration_ms"]
            ledger_adjusted_total += row["ledger_adjusted_actionable_ms"]
            longest = max(longest, row["duration_ms"])
    assert summary["total_unmuted_duration_ms"] == duration_total
    assert summary["total_freeze_overlap_ms"] == overlap_total
    assert summary["total_freeze_segment_count"] == segment_total
    assert summary["total_effective_duration_ms"] == effective_total
    assert summary["total_reopen_overlap_ms"] == reopen_overlap_total
    assert summary["total_reopen_segment_count"] == reopen_segment_total
    assert summary["total_risk_adjusted_duration_ms"] == risk_adjusted_total
    assert summary["total_rotation_overlap_ms"] == rotation_overlap_total
    assert summary["total_rotation_segment_count"] == rotation_segment_total
    assert summary["total_dispatchable_duration_ms"] == dispatchable_total
    assert summary["total_defer_overlap_ms"] == defer_overlap_total
    assert summary["total_defer_segment_count"] == defer_segment_total
    assert summary["total_actionable_duration_ms"] == actionable_total
    assert summary["total_ledger_adjusted_actionable_ms"] == ledger_adjusted_total
    assert summary["longest_window_ms"] == longest


def test_original_snapshot_preserved():
    """The frozen incident snapshot remains byte-identical to the shipped original."""
    assert ORIGINAL_WORKFLOW_PATH.exists()
    digest = hashlib.sha256(ORIGINAL_WORKFLOW_PATH.read_bytes()).hexdigest()
    assert digest == FIXTURE["broken_pipeline_sha256"]


def test_broken_snapshot_is_wrong(tmp_path: Path):
    """The frozen snapshot still produces the known-bad output, proving the pipeline was genuinely repaired."""
    _, broken_summary, _, broken_queue = _run_pipeline(tmp_path, script_path=ORIGINAL_WORKFLOW_PATH)
    broken_summary_hash = hashlib.sha256(
        json.dumps(broken_summary, sort_keys=True).encode("utf-8")
    ).hexdigest()
    assert broken_summary_hash == FIXTURE["broken_summary_sha256"]
    assert len(broken_queue) == FIXTURE["broken_queue_count"]
    assert [row.get("ticket_id") for row in broken_queue] == FIXTURE["broken_queue_ticket_ids"]
    assert broken_queue != FIXTURE["primary"]["queue_rows"]


def test_pipeline_rerun_idempotent(tmp_path: Path):
    """Two identical runs produce identical outputs."""
    _, summary_a, windows_a, queue_a = _run_pipeline(tmp_path / "a")
    _, summary_b, windows_b, queue_b = _run_pipeline(tmp_path / "b")
    assert summary_a == summary_b
    assert windows_a == windows_b
    assert queue_a == queue_b


def test_pipeline_supports_alternate_input(tmp_path: Path):
    """The reconciler generalizes to an alternate event stream it has never seen."""
    _, summary, windows, queue = _run_pipeline(tmp_path, input_path=ALT_INPUT)
    assert summary == FIXTURE["alternate"]["summary"]
    assert windows == FIXTURE["alternate"]["windows"]
    assert queue == FIXTURE["alternate"]["queue_rows"]


def test_pipeline_supports_custom_output_dir(tmp_path: Path):
    """--output-dir redirects all three artifacts to the requested directory."""
    custom = tmp_path / "custom-output"
    subprocess.run(
        [sys.executable, str(WORKFLOW_PATH), "--input", str(DEFAULT_INPUT), "--output-dir", str(custom)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert (custom / "summary.json").exists()
    assert (custom / "drift_windows.json").exists()
    assert (custom / "response_queue.jsonl").exists()


def test_cli_defaults_work_and_match_explicit_run(tmp_path: Path):
    """Default CLI arguments resolve to the documented paths and match an explicit invocation."""
    explicit_out = tmp_path / "explicit"
    explicit_out.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, str(WORKFLOW_PATH), "--input", str(DEFAULT_INPUT), "--output-dir", str(explicit_out)],
        check=True,
        capture_output=True,
        text=True,
    )
    explicit_summary = _load_json(explicit_out / "summary.json")

    subprocess.run([sys.executable, str(WORKFLOW_PATH)], check=True, capture_output=True, text=True)
    default_summary = _load_json(Path("/app/output/summary.json"))
    assert default_summary == explicit_summary


def test_freeze_source_path_affects_output(tmp_path: Path):
    """Freeze windows are read from their fixed absolute path and influence the output."""
    original = FREEZE_PATH.read_text(encoding="utf-8")
    try:
        _, summary_a, _, _ = _run_pipeline(tmp_path / "a")
        FREEZE_PATH.write_text("[]\n", encoding="utf-8")
        _, summary_b, _, _ = _run_pipeline(tmp_path / "b")
        assert summary_a["total_freeze_overlap_ms"] != summary_b["total_freeze_overlap_ms"]
        assert summary_a["freeze_compaction_checksum"] != summary_b["freeze_compaction_checksum"]
        assert (
            summary_a["total_risk_adjusted_duration_ms"]
            != summary_b["total_risk_adjusted_duration_ms"]
        )
    finally:
        FREEZE_PATH.write_text(original, encoding="utf-8")


def test_reopen_source_path_affects_output(tmp_path: Path):
    """Reopen windows are read from their fixed absolute path and influence the output."""
    original = REOPEN_PATH.read_text(encoding="utf-8")
    try:
        _, summary_a, _, queue_a = _run_pipeline(tmp_path / "a")
        REOPEN_PATH.write_text("[]\n", encoding="utf-8")
        _, summary_b, _, queue_b = _run_pipeline(tmp_path / "b")
        assert summary_a["total_reopen_overlap_ms"] > 0
        assert summary_b["total_reopen_overlap_ms"] == 0
        assert summary_a["reopen_compaction_checksum"] != summary_b["reopen_compaction_checksum"]
        assert summary_a["window_digest_checksum"] != summary_b["window_digest_checksum"]
        assert queue_a != queue_b
    finally:
        REOPEN_PATH.write_text(original, encoding="utf-8")


def test_rotation_source_path_affects_output(tmp_path: Path):
    """Rotation windows are read from their fixed absolute path and influence the output."""
    original = ROTATION_PATH.read_text(encoding="utf-8")
    try:
        _, summary_a, _, queue_a = _run_pipeline(tmp_path / "a")
        ROTATION_PATH.write_text("[]\n", encoding="utf-8")
        _, summary_b, _, queue_b = _run_pipeline(tmp_path / "b")
        assert summary_a["total_rotation_overlap_ms"] > 0
        assert summary_b["total_rotation_overlap_ms"] == 0
        assert (
            summary_a["rotation_compaction_checksum"]
            != summary_b["rotation_compaction_checksum"]
        )
        assert summary_a["window_digest_checksum"] != summary_b["window_digest_checksum"]
        assert queue_a != queue_b
    finally:
        ROTATION_PATH.write_text(original, encoding="utf-8")


def test_defer_source_path_affects_output(tmp_path: Path):
    """Defer windows are read from their fixed absolute path and influence the output."""
    original = DEFER_PATH.read_text(encoding="utf-8")
    try:
        _, summary_a, _, queue_a = _run_pipeline(tmp_path / "a")
        DEFER_PATH.write_text("[]\n", encoding="utf-8")
        _, summary_b, _, queue_b = _run_pipeline(tmp_path / "b")
        assert summary_a["total_defer_overlap_ms"] > 0
        assert summary_b["total_defer_overlap_ms"] == 0
        assert (
            summary_a["defer_compaction_checksum"]
            != summary_b["defer_compaction_checksum"]
        )
        assert summary_a["window_digest_checksum"] != summary_b["window_digest_checksum"]
        assert queue_a != queue_b
    finally:
        DEFER_PATH.write_text(original, encoding="utf-8")


def test_trust_edge_source_path_affects_output(tmp_path: Path):
    """The trust edge graph is read from its fixed absolute path and influences exposure scoring."""
    original = TRUST_EDGES_PATH.read_text(encoding="utf-8")
    try:
        _, summary_a, windows_a, queue_a = _run_pipeline(tmp_path / "a")
        TRUST_EDGES_PATH.write_text("[]\n", encoding="utf-8")
        _, summary_b, windows_b, queue_b = _run_pipeline(tmp_path / "b")
        assert summary_a["trust_edge_checksum"] != summary_b["trust_edge_checksum"]
        assert summary_a["trust_path_digest_checksum"] != summary_b[
            "trust_path_digest_checksum"
        ]
        assert windows_a != windows_b
        assert queue_a != queue_b
    finally:
        TRUST_EDGES_PATH.write_text(original, encoding="utf-8")


def test_trust_exposure_uses_strongest_bounded_simple_paths(tmp_path: Path):
    """Trust exposure scores is the max-weight node-disjoint packing of bounded simple directed paths."""
    original = TRUST_EDGES_PATH.read_text(encoding="utf-8")
    try:
        edges = [
            {"source_env": "lab", "target_env": "a", "weight": 4},
            {"source_env": "LAB", "target_env": "a", "weight": 2},
            {"source_env": "lab", "target_env": "b", "weight": 4},
            {"source_env": "a", "target_env": "target", "weight": 5},
            {"source_env": "b", "target_env": "target", "weight": 5},
            {"source_env": "a", "target_env": "deep", "weight": 3},
            {"source_env": "deep", "target_env": "vault", "weight": 2},
            {"source_env": "vault", "target_env": "too-far", "weight": 9},
            {"source_env": "target", "target_env": "lab", "weight": 9},
            {"source_env": "lab", "target_env": "lab", "weight": 9},
            {"source_env": "lab", "target_env": "ignored", "weight": 0},
        ]
        _write_json(TRUST_EDGES_PATH, edges)
        rows = [
            {
                "alert_id": "trust-1",
                "start_ms": 100,
                "end_ms": 700,
                "severity": "p1",
                "env": "lab",
                "signature": "trust traversal",
                "muted": False,
            }
        ]
        input_path = tmp_path / "trust.json"
        _write_json(input_path, rows)
        _, summary, windows, queue = _run_pipeline(
            tmp_path / "run", input_path=input_path
        )
        window = windows["lab"][0]
        assert window["trust_reachable_envs"] == [
            "a",
            "b",
            "deep",
            "target",
            "vault",
        ]
        assert window["trust_exposure_score"] == 18
        assert window["trust_strongest_path"] == ["lab", "a", "deep", "vault"]
        digest_payload = (
            "lab|18|a:4:lab>a;b:4:lab>b;deep:7:lab>a>deep;"
            "target:9:lab>a>target;vault:9:lab>a>deep>vault"
        )
        assert window["trust_path_digest"] == hashlib.sha256(
            digest_payload.encode("utf-8")
        ).hexdigest()[:12]
        assert queue[0]["trust_exposure_score"] == 18
        assert queue[0]["priority"] == "critical"
        trust_payload = (
            "a|deep|3\na|target|5\nb|target|5\ndeep|vault|2\n"
            "lab|a|4\nlab|b|4\ntarget|lab|9\nvault|too-far|9"
        )
        assert summary["trust_edge_checksum"] == hashlib.sha256(
            trust_payload.encode("utf-8")
        ).hexdigest()
    finally:
        TRUST_EDGES_PATH.write_text(original, encoding="utf-8")


def test_compacted_freeze_segments_are_used(tmp_path: Path):
    """Overlapping freeze entries are compacted into segments before overlap is measured."""
    original = FREEZE_PATH.read_text(encoding="utf-8")
    try:
        custom_freezes = [
            {"env": "lab", "start_ms": 1200, "end_ms": 1300},
            {"env": "lab", "start_ms": 1250, "end_ms": 1400},
            {"env": "lab", "start_ms": 1400, "end_ms": 1450},
            {"env": "lab", "start_ms": 1600, "end_ms": 1650},
        ]
        FREEZE_PATH.write_text(json.dumps(custom_freezes), encoding="utf-8")
        rows = [
            {
                "alert_id": "m1",
                "start_ms": 1100,
                "end_ms": 1700,
                "severity": "p2",
                "env": "lab",
                "signature": "x",
                "muted": False,
            }
        ]
        input_path = tmp_path / "edge.json"
        _write_json(input_path, rows)
        _, _, windows, queue = _run_pipeline(tmp_path / "run", input_path=input_path)
        window = windows["lab"][0]
        assert window["freeze_overlap_ms"] == 300
        assert window["freeze_segment_count"] == 2
        assert queue[0]["freeze_segment_count"] == 2
    finally:
        FREEZE_PATH.write_text(original, encoding="utf-8")


def test_reopen_compaction_and_scope_are_used(tmp_path: Path):
    """Reopen windows are compacted and applied only within their decided scope."""
    original_reopen = REOPEN_PATH.read_text(encoding="utf-8")
    try:
        reopen_rows = [
            {"env": "lab", "severity_scope": "all", "start_ms": 150, "end_ms": 200},
            {"env": "lab", "severity_scope": "all", "start_ms": 200, "end_ms": 240},
            {"env": "lab", "severity_scope": "p1", "start_ms": 260, "end_ms": 320},
            {"env": "lab", "severity_scope": "debug", "start_ms": 0, "end_ms": 1},
        ]
        REOPEN_PATH.write_text(json.dumps(reopen_rows) + "\n", encoding="utf-8")
        rows = [
            {
                "alert_id": "r1",
                "start_ms": 100,
                "end_ms": 400,
                "severity": "p1",
                "env": "lab",
                "signature": "alpha",
                "muted": False,
            },
            {
                "alert_id": "r2",
                "start_ms": 520,
                "end_ms": 820,
                "severity": "p2",
                "env": "lab",
                "signature": "beta",
                "muted": False,
            },
        ]
        input_path = tmp_path / "reopen_scope.json"
        _write_json(input_path, rows)
        _, summary, windows, queue = _run_pipeline(tmp_path / "run", input_path=input_path)
        first = windows["lab"][0]
        second = windows["lab"][1]
        assert first["reopen_overlap_ms"] == 150
        assert first["reopen_segment_count"] == 2
        assert first["risk_adjusted_duration_ms"] == 225
        assert second["reopen_overlap_ms"] == 0
        assert summary["total_reopen_overlap_ms"] == 150
        assert summary["total_reopen_segment_count"] == 2
        assert summary["reopen_compaction_checksum"] == hashlib.sha256(
            "lab|all|150|240\nlab|p1|260|320".encode("utf-8")
        ).hexdigest()
        assert [row["ticket_id"] for row in queue] == ["lab:520-820", "lab:100-400"]
    finally:
        REOPEN_PATH.write_text(original_reopen, encoding="utf-8")


def test_rotation_compaction_and_scope_are_used(tmp_path: Path):
    """Rotation windows are compacted and applied only within their decided scope."""
    original_rotation = ROTATION_PATH.read_text(encoding="utf-8")
    try:
        rotation_rows = [
            {"env": "lab", "severity_scope": "all", "start_ms": 130, "end_ms": 180},
            {"env": "lab", "severity_scope": "all", "start_ms": 178, "end_ms": 240},
            {"env": "lab", "severity_scope": "p1", "start_ms": 260, "end_ms": 310},
            {"env": "lab", "severity_scope": "debug", "start_ms": 0, "end_ms": 1},
        ]
        ROTATION_PATH.write_text(json.dumps(rotation_rows) + "\n", encoding="utf-8")
        rows = [
            {
                "alert_id": "z1",
                "start_ms": 100,
                "end_ms": 400,
                "severity": "p1",
                "env": "lab",
                "signature": "alpha",
                "muted": False,
            },
            {
                "alert_id": "z2",
                "start_ms": 520,
                "end_ms": 840,
                "severity": "p2",
                "env": "lab",
                "signature": "beta",
                "muted": False,
            },
        ]
        input_path = tmp_path / "rotation_scope.json"
        _write_json(input_path, rows)
        _, summary, windows, queue = _run_pipeline(tmp_path / "run", input_path=input_path)
        first = windows["lab"][0]
        second = windows["lab"][1]
        assert first["rotation_overlap_ms"] == 160
        assert first["rotation_segment_count"] == 2
        assert first["dispatchable_duration_ms"] == max(
            first["risk_adjusted_duration_ms"] - (-(-160 // 3)), 0
        )
        assert second["rotation_overlap_ms"] == 0
        assert summary["total_rotation_overlap_ms"] == 160
        assert summary["total_rotation_segment_count"] == 2
        assert summary["rotation_compaction_checksum"] == hashlib.sha256(
            "lab|all|130|240\nlab|p1|260|310".encode("utf-8")
        ).hexdigest()
        assert [row["ticket_id"] for row in queue] == ["lab:520-840", "lab:100-400"]
    finally:
        ROTATION_PATH.write_text(original_rotation, encoding="utf-8")


def test_defer_compaction_and_scope_are_used(tmp_path: Path):
    """Defer windows are compacted and applied only within their decided scope."""
    original_defer = DEFER_PATH.read_text(encoding="utf-8")
    try:
        defer_rows = [
            {"env": "lab", "severity_scope": "all", "start_ms": 110, "end_ms": 200},
            {"env": "lab", "severity_scope": "all", "start_ms": 200, "end_ms": 250},
            {"env": "lab", "severity_scope": "p1", "start_ms": 255, "end_ms": 325},
            {"env": "lab", "severity_scope": "debug", "start_ms": 0, "end_ms": 1},
        ]
        DEFER_PATH.write_text(json.dumps(defer_rows) + "\n", encoding="utf-8")
        rows = [
            {
                "alert_id": "q1",
                "start_ms": 100,
                "end_ms": 420,
                "severity": "p1",
                "env": "lab",
                "signature": "alpha",
                "muted": False,
            },
            {
                "alert_id": "q2",
                "start_ms": 540,
                "end_ms": 900,
                "severity": "p2",
                "env": "lab",
                "signature": "beta",
                "muted": False,
            },
        ]
        input_path = tmp_path / "defer_scope.json"
        _write_json(input_path, rows)
        _, summary, windows, queue = _run_pipeline(tmp_path / "run", input_path=input_path)
        first = windows["lab"][0]
        second = windows["lab"][1]
        assert first["defer_overlap_ms"] == 210
        assert first["defer_segment_count"] == 2
        assert first["actionable_duration_ms"] == max(
            first["dispatchable_duration_ms"] - (-(-210 // 4)), 0
        )
        assert second["defer_overlap_ms"] == 0
        assert summary["total_defer_overlap_ms"] == 210
        assert summary["total_defer_segment_count"] == 2
        assert summary["defer_compaction_checksum"] == hashlib.sha256(
            "lab|all|110|250\nlab|p1|255|325".encode("utf-8")
        ).hexdigest()
        assert [row["ticket_id"] for row in queue] == ["lab:540-900", "lab:100-420"]
    finally:
        DEFER_PATH.write_text(original_defer, encoding="utf-8")


def test_p2_windows_borrow_p1_scope_when_p2_scope_missing(tmp_path: Path):
    """P2 windows inherit the p1 scope allowlist when no p2-specific scope is defined."""
    originals = {
        REOPEN_PATH: REOPEN_PATH.read_text(encoding="utf-8"),
        ROTATION_PATH: ROTATION_PATH.read_text(encoding="utf-8"),
        DEFER_PATH: DEFER_PATH.read_text(encoding="utf-8"),
        FREEZE_PATH: FREEZE_PATH.read_text(encoding="utf-8"),
    }
    try:
        _write_json(FREEZE_PATH, [])
        _write_json(
            REOPEN_PATH,
            [
                {
                    "env": "lab",
                    "severity_scope": "p1",
                    "start_ms": 150,
                    "end_ms": 250,
                }
            ],
        )
        _write_json(
            ROTATION_PATH,
            [
                {
                    "env": "lab",
                    "severity_scope": "p1",
                    "start_ms": 150,
                    "end_ms": 250,
                }
            ],
        )
        _write_json(
            DEFER_PATH,
            [
                {
                    "env": "lab",
                    "severity_scope": "p1",
                    "start_ms": 150,
                    "end_ms": 250,
                }
            ],
        )
        rows = [
            {
                "alert_id": "fb1",
                "start_ms": 100,
                "end_ms": 400,
                "severity": "p2",
                "env": "lab",
                "signature": "fallback",
                "muted": False,
            }
        ]
        input_path = tmp_path / "fallback_scope.json"
        _write_json(input_path, rows)
        _, summary, windows, queue = _run_pipeline(tmp_path / "run", input_path=input_path)
        window = windows["lab"][0]
        assert window["reopen_overlap_ms"] == 100
        assert window["rotation_overlap_ms"] == 100
        assert window["defer_overlap_ms"] == 100
        assert window["risk_adjusted_duration_ms"] == 250
        # Reopen and rotation both cover 150-250 here, so under #CR-5382 the whole
        # rotation span is already attenuated as reopen and the rotation subtraction
        # is zero -- while rotation_overlap_ms above still reports the full 100.
        assert window["dispatchable_duration_ms"] == 250
        assert window["actionable_duration_ms"] == 225
        assert summary["queued_window_count"] == 0
        assert queue == []
    finally:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")


def test_tie_break_full_tie_keeps_first_seen(tmp_path: Path):
    """A full tie across every dedupe key keeps the first-seen record."""
    rows = [
        {
            "alert_id": "t1",
            "start_ms": 100,
            "end_ms": 500,
            "severity": "p2",
            "env": "prod",
            "signature": "ab cd",
            "muted": False,
        },
        {
            "alert_id": "t1",
            "start_ms": 900,
            "end_ms": 500,
            "severity": "p2",
            "env": "prod",
            "signature": "ef gh",
            "muted": True,
        },
    ]
    input_path = tmp_path / "tie.json"
    _write_json(input_path, rows)
    _, summary, windows, _ = _run_pipeline(tmp_path / "run", input_path=input_path)
    assert summary["canonical_alert_count"] == 1
    assert summary["muted_excluded_count"] == 0
    assert windows["prod"][0]["start_ms"] == 100


def test_muted_truthiness_non_string_non_bool(tmp_path: Path):
    """Muted flags holding non-string, non-boolean values are coerced by the decided truthiness rules."""
    rows = [
        {
            "alert_id": "m1",
            "start_ms": 100,
            "end_ms": 500,
            "severity": "p1",
            "env": "prod",
            "signature": "x",
            "muted": 0,
        },
        {
            "alert_id": "m2",
            "start_ms": 100,
            "end_ms": 500,
            "severity": "p1",
            "env": "prod",
            "signature": "y",
            "muted": 3,
        },
    ]
    input_path = tmp_path / "muted.json"
    _write_json(input_path, rows)
    _, summary, windows, _ = _run_pipeline(tmp_path / "run", input_path=input_path)
    assert summary["muted_excluded_count"] == 1
    assert sum(w["alert_count"] for env_windows in windows.values() for w in env_windows) == 1


def test_stateful_risk_ledger_propagates_and_decays_between_windows(tmp_path: Path):
    """The risk ledger carries state between a service's windows with idle-gap decay and the carry-out cap."""
    originals = {
        FREEZE_PATH: FREEZE_PATH.read_text(encoding="utf-8"),
        REOPEN_PATH: REOPEN_PATH.read_text(encoding="utf-8"),
        ROTATION_PATH: ROTATION_PATH.read_text(encoding="utf-8"),
        DEFER_PATH: DEFER_PATH.read_text(encoding="utf-8"),
    }
    try:
        for path in originals:
            path.write_text("[]\n", encoding="utf-8")
        rows = [
            {
                "alert_id": "l1",
                "start_ms": 100,
                "end_ms": 400,
                "severity": "p1",
                "env": "lab",
                "signature": "first",
                "muted": False,
            },
            {
                "alert_id": "l2",
                "start_ms": 600,
                "end_ms": 850,
                "severity": "p2",
                "env": "lab",
                "signature": "second",
                "muted": False,
            },
        ]
        input_path = tmp_path / "ledger.json"
        _write_json(input_path, rows)
        _, summary, windows, queue = _run_pipeline(
            tmp_path / "run", input_path=input_path
        )
        first, second = windows["lab"]
        assert first["idle_gap_ms"] == 0
        assert first["carry_in_ms"] == 0
        assert first["carry_out_ms"] == 300
        assert second["idle_gap_ms"] == 200
        assert second["carry_in_ms"] == 200
        assert second["ledger_adjusted_actionable_ms"] == 300
        assert second["carry_out_ms"] == 450
        assert summary["max_carry_out_ms"] == 450
        assert len(summary["ledger_checksum"]) == 64
        second_queue = next(row for row in queue if row["ticket_id"] == "lab:600-850")
        assert second_queue["ledger_pressure_score"] == (-(-450 // 60)) + (-(-200 // 120))
    finally:
        for path, content in originals.items():
            path.write_text(content, encoding="utf-8")


def test_pipeline_does_not_reference_test_artifacts():
    """The reconciler source never references the verifier's /tests or /solution trees."""
    code = WORKFLOW_PATH.read_text(encoding="utf-8")
    for token in ("/tests", "fixtures/alt_events.json", "expected_summary.json"):
        assert token not in code



ROOT_AUTH_KEYS = Path("/root/.ssh/authorized_keys")
SUDOERS_DROPIN = Path("/etc/sudoers.d/cert-rotation")
ROTATION_KEY = Path("/app/secrets/rotation_signing.key")


def test_rogue_ssh_key_revoked():
    """The rogue automation key is removed and the legitimate operator key survives.

    Deleting authorized_keys wholesale is not an acceptable containment: the runbook
    requires the rollout automation's key to be revoked while every other key is left
    in place, so this asserts the file still exists and still authorises the
    platform-oncall operator.
    """
    assert ROOT_AUTH_KEYS.exists(), "authorized_keys was deleted; other keys must be preserved"
    text = ROOT_AUTH_KEYS.read_text()
    assert "cert-rotation-automation" not in text, "rogue automation key still authorised"
    assert "platform-oncall" in text, "legitimate operator key was removed with the rogue one"
    remaining = [ln for ln in text.splitlines() if ln.strip()]
    assert len(remaining) == 1, f"expected exactly the operator key to remain, found {len(remaining)}"


def test_sudoers_escalation_removed():
    """The passwordless sudoers drop-in for svc-certd is deleted entirely."""
    assert not SUDOERS_DROPIN.exists()


def test_rotation_key_locked_down():
    """The rotation signing key is retained but restricted to root-only 0600."""
    import pwd

    assert ROTATION_KEY.exists(), "operations still needs the token in place"
    info = ROTATION_KEY.stat()
    assert (info.st_mode & 0o777) == 0o600
    assert info.st_uid == pwd.getpwnam("root").pw_uid
    assert info.st_gid == pwd.getpwnam("root").pw_gid


def test_governing_entry_index_is_complete():
    """Every governing (non-superseded) review entry is reachable from the index.

    The instruction directs agents to the index, so an entry missing from it is
    effectively undiscoverable no matter how clearly the log states the rule.
    """
    import re
    gi = SPEC["governing_entry_index"]
    listed = {e for v in gi["stages"].values() for e in v}
    nonnorm = set()
    log_text = Path("/app/incident/rotation_review_log.md").read_text(encoding="utf-8")
    pattern = re.compile(r"\*\*[A-Za-z -]+? \(\d{4}-\d{2}-\d{2} - (#CR-\d+)\)\*\*([^\n]*)")
    governing = {
        m.group(1)
        for m in pattern.finditer(log_text)
        if "*(superseded" not in m.group(2).lower() and "*(revised" not in m.group(2).lower()
    }
    assert governing, "no governing entries found -- parser drifted from the log format"
    missing = sorted(governing - listed - nonnorm)
    assert not missing, f"governing entries absent from governing_entry_index: {missing}"


def test_reopen_rotation_precedence_is_live_in_the_shipped_data():
    """#CR-5382 must actually bind: the two layers overlap somewhere in the inputs.

    A precedence rule that no input exercises is documented but dormant, so it
    contributes nothing and silently stops being verified. This pins the data.
    """
    reopen = json.loads(Path("/app/data/reopen_windows.json").read_text(encoding="utf-8"))
    rotation = json.loads(Path("/app/data/rotation_windows.json").read_text(encoding="utf-8"))
    shared = 0
    for rot in rotation:
        for rep in reopen:
            if rot["env"] != rep["env"]:
                continue
            shared += max(0, min(rot["end_ms"], rep["end_ms"]) - max(rot["start_ms"], rep["start_ms"]))
    assert shared > 0, "no reopen/rotation overlap in the inputs -- #CR-5382 is dormant"


def test_rotation_overlap_is_reported_unadjusted(primary_outputs):
    """#CR-5382 changes the subtraction only; rotation_overlap_ms stays unadjusted."""
    _, summary, windows, _ = primary_outputs
    blocks = [b for v in windows.values() for b in v]
    assert summary["total_rotation_overlap_ms"] == sum(b["rotation_overlap_ms"] for b in blocks)
    # Recompute the unadjusted compacted overlap straight from the inputs, so this
    # cannot pass on a reported value that was silently reduced by the precedence.
    rotation = json.loads(Path("/app/data/rotation_windows.json").read_text(encoding="utf-8"))
    independent = 0
    for env, blocks_for_env in windows.items():
        for block in blocks_for_env:
            spans = [
                (max(block["start_ms"], r["start_ms"]), min(block["end_ms"], r["end_ms"]))
                for r in rotation
                if r["env"] == env and r["severity_scope"] in ("all", block["max_severity"])
            ]
            spans = [(s, e) for s, e in spans if e > s]
            spans.sort()
            merged: list[list[int]] = []
            for s, e in spans:
                if merged and s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            independent += sum(e - s for s, e in merged)
    assert summary["total_rotation_overlap_ms"] == independent, (
        "reported rotation overlap was adjusted by the precedence rule; "
        "#CR-5382 requires it to stay unadjusted"
    )


def test_env_queue_cap_applied_after_ordering(primary_outputs):
    """#CR-5384: at most three rows per env, retained by the GLOBAL order."""
    _, _, _, queue = primary_outputs
    per_env = {}
    for row in queue:
        per_env[row["env"]] = per_env.get(row["env"], 0) + 1
    assert per_env, "queue is empty"
    assert max(per_env.values()) <= 3, f"env exceeded the cap: {per_env}"
    assert any(v == 3 for v in per_env.values()), "cap never binds -- it is dormant"
