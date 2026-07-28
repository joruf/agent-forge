"""Persistent model throughput statistics."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agentforge.config import settings


def _utcnow() -> str:
    """
    Return the current UTC timestamp in ISO format.

    :return: ISO-8601 timestamp string
    """
    return datetime.now(timezone.utc).isoformat()


class PerformanceStore:
    """Store measured model accessibility and throughput samples."""

    def __init__(self, config_path: Path | None = None) -> None:
        """
        Initialize the performance store.

        :param config_path: Optional JSON file path override
        """
        self.config_path = config_path or (settings.data_dir / "model_performance.json")
        self._data = self._load()

    def _load(self) -> dict[str, Any]:
        """
        Load performance data from disk.

        :return: Parsed performance payload
        """
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.config_path.exists():
            return {"models": {}}
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        """Persist performance data to disk."""
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def reload(self) -> None:
        """Reload performance data from disk."""
        self._data = self._load()

    def list_models(self) -> list[dict[str, Any]]:
        """
        Return stored performance entries sorted by display name.

        :return: Performance entry list
        """
        models = self._data.get("models") or {}
        return sorted(
            models.values(),
            key=lambda entry: str(entry.get("display_name") or entry.get("model") or "").lower(),
        )

    def get_model(self, model: str) -> dict[str, Any] | None:
        """
        Return one stored performance entry.

        :param model: LiteLLM model identifier
        :return: Performance entry or None
        """
        models = self._data.get("models") or {}
        return models.get(model.strip())

    def record(
        self,
        model: str,
        *,
        display_name: str | None = None,
        accessible: bool,
        tokens_per_second: float | None = None,
        source: str,
        error: str | None = None,
    ) -> dict[str, Any]:
        """
        Store or update one model performance entry.

        :param model: LiteLLM model identifier
        :param display_name: Optional display label
        :param accessible: Whether the model responded successfully
        :param tokens_per_second: Measured throughput
        :param source: Measurement source such as benchmark or runtime
        :param error: Optional last error message
        :return: Stored performance entry
        """
        key = model.strip()
        existing = (self._data.get("models") or {}).get(key, {})
        sample_count = int(existing.get("sample_count") or 0)
        previous_tps = existing.get("tokens_per_second")

        merged_tps = previous_tps
        if tokens_per_second is not None and tokens_per_second > 0:
            if previous_tps and source == "runtime":
                merged_tps = round((float(previous_tps) * 0.7) + (tokens_per_second * 0.3), 2)
            else:
                merged_tps = round(float(tokens_per_second), 2)
            sample_count += 1
        elif source == "benchmark" and accessible:
            sample_count = max(sample_count, 1)

        entry = {
            "model": key,
            "display_name": display_name or existing.get("display_name") or key,
            "accessible": accessible,
            "tokens_per_second": merged_tps,
            "sample_count": sample_count,
            "last_measured_at": _utcnow(),
            "source": source,
            "last_error": error,
        }
        self._data.setdefault("models", {})[key] = entry
        self._save()
        return entry

    def record_runtime(self, model: str, output_chars: int, duration_seconds: float) -> None:
        """
        Record a runtime throughput sample from streamed output.

        :param model: LiteLLM model identifier
        :param output_chars: Generated character count
        :param duration_seconds: Stream duration in seconds
        """
        if duration_seconds <= 0 or output_chars <= 0:
            return
        estimated_tokens = max(1, output_chars // 4)
        tokens_per_second = estimated_tokens / duration_seconds
        self.record(
            model,
            accessible=True,
            tokens_per_second=tokens_per_second,
            source="runtime",
        )


performance_store = PerformanceStore()
