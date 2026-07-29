"""Tests for model performance storage and reporting."""

from __future__ import annotations

import pytest

from agentforge.config import settings
from agentforge.services.model_performance_service import (
    build_performance_report,
    claim_startup_benchmark,
    record_runtime_performance,
)
from agentforge.storage.performance_store import PerformanceStore


@pytest.fixture
def performance_store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> PerformanceStore:
    """
    Provide an isolated performance store for tests.

    :param tmp_path: Pytest temporary directory
    :param monkeypatch: Pytest monkeypatch fixture
    :return: Performance store instance
    """
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    return PerformanceStore(tmp_path / "model_performance.json")


def test_performance_store_records_benchmark(performance_store: PerformanceStore) -> None:
    """Benchmark samples are persisted with throughput values."""
    entry = performance_store.record(
        "ollama/llama3.1:8b",
        display_name="Llama 3.1",
        accessible=True,
        tokens_per_second=42.5,
        source="benchmark",
    )

    assert entry["tokens_per_second"] == 42.5
    assert entry["accessible"] is True
    assert performance_store.get_model("ollama/llama3.1:8b") is not None


def test_performance_store_blends_runtime_samples(performance_store: PerformanceStore) -> None:
    """Runtime samples update stored throughput with smoothing."""
    performance_store.record(
        "ollama/llama3.1:8b",
        accessible=True,
        tokens_per_second=40.0,
        source="benchmark",
    )
    performance_store.record_runtime("ollama/llama3.1:8b", 400, 5.0)

    entry = performance_store.get_model("ollama/llama3.1:8b")
    assert entry is not None
    assert entry["sample_count"] >= 2
    assert entry["source"] == "runtime"
    assert entry["tokens_per_second"] is not None


def test_performance_store_keeps_bounded_recent_sample_history(
    performance_store: PerformanceStore,
) -> None:
    """Only the last MAX_RECENT_SAMPLES throughput samples are kept, most-recent last."""
    from agentforge.storage.performance_store import MAX_RECENT_SAMPLES

    for value in range(1, MAX_RECENT_SAMPLES + 3):
        performance_store.record(
            "ollama/llama3.1:8b",
            accessible=True,
            tokens_per_second=float(value),
            source="benchmark",
        )

    entry = performance_store.get_model("ollama/llama3.1:8b")
    assert entry is not None
    samples = entry["recent_samples"]
    assert len(samples) == MAX_RECENT_SAMPLES
    assert [s["tokens_per_second"] for s in samples] == [3.0, 4.0, 5.0, 6.0, 7.0]


def test_claim_startup_benchmark_only_once(monkeypatch) -> None:
    """Only the first caller this process lifetime claims the startup benchmark."""
    monkeypatch.setattr(
        "agentforge.services.model_performance_service._startup_benchmark_triggered", False,
    )
    assert claim_startup_benchmark() is True
    assert claim_startup_benchmark() is False
    assert claim_startup_benchmark() is False


def test_record_runtime_performance_ignores_empty_output(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty streams must not create performance entries."""
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    isolated = PerformanceStore(tmp_path / "model_performance.json")
    monkeypatch.setattr(
        "agentforge.services.model_performance_service.performance_store",
        isolated,
    )
    record_runtime_performance("ollama/test", 0, 1.0)
    assert isolated.list_models() == []


def test_build_performance_report_counts_measured_models(
    performance_store: PerformanceStore,
) -> None:
    """Reports include measured and total model counts."""
    performance_store.record(
        "ollama/fast",
        accessible=True,
        tokens_per_second=30.0,
        source="benchmark",
    )
    performance_store.record(
        "ollama/slow",
        accessible=False,
        tokens_per_second=None,
        source="benchmark",
        error="offline",
    )

    report = build_performance_report(performance_store.list_models())

    assert report["total_count"] == 2
    assert report["measured_count"] == 1
