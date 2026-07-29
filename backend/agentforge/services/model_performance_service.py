"""Benchmark and runtime collection for model throughput."""

from __future__ import annotations

import time
from typing import Any

import httpx

from agentforge.config import settings
from agentforge.llm.cloud_providers import apply_cloud_credentials, detect_provider_from_model, get_api_key
from agentforge.llm.model_router import ModelRouter
from agentforge.services.setup_service import _parse_ollama_tag, test_litellm_import, test_ollama
from agentforge.storage.model_store import model_store
from agentforge.storage.performance_store import performance_store

_BENCHMARK_PROMPT = "Reply with one word: OK"
_startup_benchmark_triggered = False


def claim_startup_benchmark() -> bool:
    """
    Claim the one-time startup benchmark slot for this backend process.

    Guards against every open frontend tab independently triggering its own
    full benchmark run — only the first caller (across all tabs/clients) gets
    True; every later call this process lifetime gets False.

    :return: True if this call may proceed with the startup benchmark
    """
    global _startup_benchmark_triggered
    if _startup_benchmark_triggered:
        return False
    _startup_benchmark_triggered = True
    return True


async def collect_benchmark_targets() -> list[tuple[str, str]]:
    """
    Collect unique LiteLLM models that should appear in performance reports.

    :return: List of (model id, display name) tuples
    """
    router = ModelRouter()
    installed = await router.list_installed_models()
    targets: dict[str, str] = {}

    default_model = settings.default_model.strip()
    if default_model:
        targets[default_model] = default_model

    model_store.reload()
    for entry in model_store.list_models():
        if not entry.get("enabled", True):
            continue
        tag = str(entry.get("ollama_tag") or "").strip()
        if not tag:
            continue
        litellm_model = f"ollama/{tag}"
        targets[litellm_model] = str(entry.get("display_name") or tag)

    for routed_model in model_store.get_routing().values():
        routed = str(routed_model or "").strip()
        if routed:
            targets[routed] = routed

    for tag in installed:
        litellm_model = f"ollama/{tag}"
        targets.setdefault(litellm_model, tag)

    return sorted(targets.items(), key=lambda item: item[1].lower())


async def _benchmark_ollama_model(
    model_ref: str,
    tag: str,
    base_url: str,
) -> tuple[bool, float | None, str | None]:
    """
    Benchmark one Ollama model with a short generate request.

    :param model_ref: LiteLLM model identifier
    :param tag: Ollama model tag
    :param base_url: Ollama base URL
    :return: Tuple of success flag, tokens per second, and optional error
    """
    timeout = max(15.0, float(settings.llm_request_timeout))
    try:
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/api/generate",
                json={"model": tag, "prompt": _BENCHMARK_PROMPT, "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
        eval_count = int(payload.get("eval_count") or 0)
        eval_duration_ns = int(payload.get("eval_duration") or 0)
        if eval_duration_ns > 0 and eval_count > 0:
            tokens_per_second = eval_count / (eval_duration_ns / 1_000_000_000)
            return True, round(tokens_per_second, 2), None

        response_text = str(payload.get("response") or "")
        elapsed = max(time.monotonic() - started, 0.001)
        estimated_tokens = max(1, len(response_text) // 4)
        return True, round(estimated_tokens / elapsed, 2), None
    except Exception as exc:
        return False, None, str(exc)


async def _benchmark_cloud_model(model_ref: str) -> tuple[bool, float | None, str | None]:
    """
    Benchmark one cloud model through LiteLLM.

    :param model_ref: LiteLLM model identifier
    :return: Tuple of success flag, tokens per second, and optional error
    """
    provider = detect_provider_from_model(model_ref)
    if provider is None or not get_api_key(provider):
        return False, None, "Missing API credentials"

    import_result = test_litellm_import()
    if not import_result.get("ok"):
        return False, None, str(import_result.get("message") or "LiteLLM unavailable")

    try:
        import litellm

        apply_cloud_credentials()
        started = time.monotonic()
        request_timeout = min(60.0, float(settings.llm_request_timeout))
        response = await litellm.acompletion(
            model=model_ref,
            messages=[{"role": "user", "content": _BENCHMARK_PROMPT}],
            max_tokens=8,
            timeout=request_timeout,
        )
        elapsed = max(time.monotonic() - started, 0.001)
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
        token_count = int(completion_tokens) if completion_tokens else max(1, len(content) // 4)
        return True, round(token_count / elapsed, 2), None
    except Exception as exc:
        return False, None, str(exc)


async def benchmark_model(model_ref: str, display_name: str | None = None) -> dict[str, Any]:
    """
    Benchmark one configured model and persist the result.

    :param model_ref: LiteLLM model identifier
    :param display_name: Optional display label
    :return: Stored performance entry
    """
    label = display_name or model_ref
    provider = detect_provider_from_model(model_ref)
    if provider is not None:
        accessible, tokens_per_second, error = await _benchmark_cloud_model(model_ref)
        return performance_store.record(
            model_ref,
            display_name=label,
            accessible=accessible,
            tokens_per_second=tokens_per_second,
            source="benchmark",
            error=error,
        )

    tag = _parse_ollama_tag(model_ref)
    if not tag:
        return performance_store.record(
            model_ref,
            display_name=label,
            accessible=False,
            tokens_per_second=None,
            source="benchmark",
            error="Unsupported model reference",
        )

    ollama_result = await test_ollama(settings.ollama_base_url)
    installed = set(ollama_result.get("models") or [])
    if tag not in installed:
        return performance_store.record(
            model_ref,
            display_name=label,
            accessible=False,
            tokens_per_second=None,
            source="benchmark",
            error="Model not installed on Ollama",
        )

    accessible, tokens_per_second, error = await _benchmark_ollama_model(
        model_ref,
        tag,
        settings.ollama_base_url,
    )
    return performance_store.record(
        model_ref,
        display_name=label,
        accessible=accessible,
        tokens_per_second=tokens_per_second,
        source="benchmark",
        error=error,
    )


async def _benchmark_events() -> Any:
    """
    Yield benchmark progress and completion events for each configured model.

    :yield: Progress or completion event dictionaries
    """
    targets = await collect_benchmark_targets()
    total = len(targets)
    models: list[dict[str, Any]] = []

    if total == 0:
        report = build_performance_report([])
        yield {"type": "complete", "report": report}
        return

    for index, (model_ref, display_name) in enumerate(targets, start=1):
        entry = await benchmark_model(model_ref, display_name)
        models.append(entry)
        yield {
            "type": "progress",
            "completed": index,
            "total": total,
            "current_model": display_name,
            "entry": entry,
        }

    yield {
        "type": "complete",
        "report": build_performance_report(models),
    }


async def benchmark_all_models() -> dict[str, Any]:
    """
    Benchmark all known configured models.

    :return: Performance report payload
    """
    report: dict[str, Any] | None = None
    async for event in _benchmark_events():
        if event.get("type") == "complete":
            report = event.get("report")
    return report or build_performance_report([])


async def stream_benchmark_all_models():
    """
    Stream benchmark progress as newline-delimited JSON events.

    :yield: Serialized JSON event lines
    """
    import json

    async for event in _benchmark_events():
        yield json.dumps(event, ensure_ascii=False) + "\n"


def build_performance_report(models: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """
    Build a performance report from stored entries.

    :param models: Optional explicit model list override
    :return: Serializable performance report
    """
    entries = models if models is not None else performance_store.list_models()
    measured = [entry for entry in entries if entry.get("tokens_per_second")]
    return {
        "models": entries,
        "measured_count": len(measured),
        "total_count": len(entries),
    }


def get_performance_report() -> dict[str, Any]:
    """
    Return the latest stored performance report.

    :return: Serializable performance report
    """
    performance_store.reload()
    return build_performance_report()


def record_runtime_performance(model: str, output_chars: int, duration_seconds: float) -> None:
    """
    Record runtime throughput from one completed model stream.

    :param model: LiteLLM model identifier
    :param output_chars: Generated character count
    :param duration_seconds: Stream duration in seconds
    """
    if not model.strip():
        return
    performance_store.record_runtime(model.strip(), output_chars, duration_seconds)
