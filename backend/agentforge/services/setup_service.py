"""Setup wizard connectivity and configuration tests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from agentforge.config import settings
from agentforge.i18n import current_locale, t
from agentforge.llm.cloud_providers import (
    CLOUD_PROVIDERS,
    CloudProvider,
    apply_cloud_credentials,
    detect_provider_from_model,
    get_api_key,
    get_provider,
)
from agentforge.storage.model_store import model_store


def _label(test_id: str) -> str:
    """Return localized test label."""
    return t(f"setup.labels.{test_id}", locale=current_locale())


def _provider_label(provider: CloudProvider) -> str:
    """Return localized label for a cloud provider test."""
    return t(f"setup.labels.{provider.id}", locale=current_locale())


@asynccontextmanager
async def _temporary_cloud_keys(overrides: dict[str, str | None]) -> AsyncIterator[None]:
    """
    Temporarily apply cloud API key overrides for test runs.

    :param overrides: Mapping of settings field names to API key values
    """
    previous: dict[str, str] = {}
    for field_name, value in overrides.items():
        if value is None:
            continue
        previous[field_name] = str(getattr(settings, field_name, "") or "")
        setattr(settings, field_name, value)
    try:
        yield
    finally:
        for field_name, value in previous.items():
            setattr(settings, field_name, value)


async def test_backend() -> dict[str, Any]:
    """Verify backend is responding."""
    return {
        "id": "backend",
        "label": _label("backend"),
        "ok": True,
        "message": t("setup.backend_ok"),
    }


async def test_ollama(url: str | None = None) -> dict[str, Any]:
    """
    Test Ollama server connectivity and list models.

    :param url: Optional Ollama base URL override
    :return: Test result dict
    """
    base = (url or settings.ollama_base_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{base}/api/tags")
            response.raise_for_status()
            payload = response.json()
        models = [item.get("name", "") for item in payload.get("models", []) if item.get("name")]
        if not models:
            return {
                "id": "ollama",
                "label": _label("ollama"),
                "ok": True,
                "warning": True,
                "message": t("setup.ollama_empty", url=base),
                "models": [],
                "count": 0,
            }
        return {
            "id": "ollama",
            "label": _label("ollama"),
            "ok": True,
            "message": t("setup.ollama_ok", count=len(models), url=base),
            "models": models,
            "count": len(models),
        }
    except Exception as exc:
        return {
            "id": "ollama",
            "label": _label("ollama"),
            "ok": False,
            "message": t("setup.ollama_fail", url=base, error=str(exc)),
            "models": [],
            "count": 0,
        }


async def test_cloud_provider(provider: CloudProvider) -> dict[str, Any]:
    """
    Test a cloud provider API key if configured.

    :param provider: Cloud provider metadata
    :return: Test result dict
    """
    if not get_api_key(provider):
        return {
            "id": provider.id,
            "label": _provider_label(provider),
            "ok": None,
            "skipped": True,
            "message": t("setup.cloud_skipped", provider=provider.id.title()),
        }

    try:
        import litellm

        apply_cloud_credentials()
        response = await litellm.acompletion(
            model=provider.test_model,
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=5,
            timeout=settings.llm_request_timeout,
        )
        content = response.choices[0].message.content or ""
        return {
            "id": provider.id,
            "label": _provider_label(provider),
            "ok": True,
            "message": t("setup.cloud_ok", provider=provider.id.title(), response=content[:20]),
        }
    except Exception as exc:
        return {
            "id": provider.id,
            "label": _provider_label(provider),
            "ok": False,
            "message": t("setup.cloud_fail", provider=provider.id.title(), error=str(exc)),
        }


async def test_openai() -> dict[str, Any]:
    """Test OpenAI API key if configured."""
    provider = get_provider("openai")
    if provider is None:
        return {
            "id": "openai",
            "label": _label("openai"),
            "ok": None,
            "skipped": True,
            "message": t("setup.openai_skipped"),
        }
    return await test_cloud_provider(provider)


async def test_all_cloud_providers() -> list[dict[str, Any]]:
    """
    Test all configured cloud provider API keys.

    :return: List of per-provider test results
    """
    results: list[dict[str, Any]] = []
    for provider in CLOUD_PROVIDERS:
        results.append(await test_cloud_provider(provider))
    return results


async def test_cloud_inference(model_ref: str) -> dict[str, Any]:
    """
    Run a minimal inference request against a cloud default model.

    :param model_ref: LiteLLM model reference
    :return: Test result dict
    """
    provider = detect_provider_from_model(model_ref)
    if provider is None:
        return {
            "id": "cloud_inference",
            "label": t("settings_test.labels.cloud_inference"),
            "ok": None,
            "skipped": True,
            "message": t("settings_test.cloud_inference_skip"),
        }
    if not get_api_key(provider):
        return {
            "id": "cloud_inference",
            "label": t("settings_test.labels.cloud_inference"),
            "ok": False,
            "message": t(
                "settings_test.cloud_inference_no_key",
                provider=provider.id.title(),
                model=model_ref,
            ),
            "model": model_ref,
        }

    try:
        import litellm

        apply_cloud_credentials()
        response = await litellm.acompletion(
            model=model_ref,
            messages=[{"role": "user", "content": "Say OK"}],
            max_tokens=5,
            timeout=settings.llm_request_timeout,
        )
        content = (response.choices[0].message.content or "")[:40]
        return {
            "id": "cloud_inference",
            "label": t("settings_test.labels.cloud_inference"),
            "ok": True,
            "message": t("settings_test.cloud_inference_ok", model=model_ref, response=content),
            "model": model_ref,
        }
    except Exception as exc:
        return {
            "id": "cloud_inference",
            "label": t("settings_test.labels.cloud_inference"),
            "ok": False,
            "message": t("settings_test.cloud_inference_fail", model=model_ref, error=str(exc)),
            "model": model_ref,
        }


def test_workspace(path: str | None = None) -> dict[str, Any]:
    """
    Test workspace directory exists and is writable.

    :param path: Optional workspace path override
    :return: Test result dict
    """
    workspace = Path(path or settings.workspace_root)
    if not workspace.exists():
        return {
            "id": "workspace",
            "label": _label("workspace"),
            "ok": False,
            "message": t("setup.workspace_missing", path=str(workspace)),
        }
    if not workspace.is_dir():
        return {
            "id": "workspace",
            "label": _label("workspace"),
            "ok": False,
            "message": t("setup.workspace_not_dir", path=str(workspace)),
        }
    test_file = workspace / ".agentforge_write_test"
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink()
        return {
            "id": "workspace",
            "label": _label("workspace"),
            "ok": True,
            "message": t("setup.workspace_ok", path=str(workspace)),
        }
    except Exception as exc:
        return {
            "id": "workspace",
            "label": _label("workspace"),
            "ok": False,
            "message": t("setup.workspace_fail", path=str(workspace), error=str(exc)),
        }


async def test_ollama_generate(
    model_tag: str | None = None,
    url: str | None = None,
) -> dict[str, Any]:
    """
    Run a minimal generate request against Ollama.

    :param model_tag: Ollama model tag to test
    :param url: Optional Ollama base URL override
    :return: Test result dict
    """
    base = (url or settings.ollama_base_url).rstrip("/")
    ollama_result = await test_ollama(base)
    if not ollama_result.get("ok"):
        return {
            "id": "ollama_generate",
            "label": _label("ollama_generate"),
            "ok": False,
            "message": t("setup.generate_skip_ollama"),
        }

    models = ollama_result.get("models") or []
    if not models:
        return {
            "id": "ollama_generate",
            "label": _label("ollama_generate"),
            "ok": None,
            "skipped": True,
            "message": t("setup.generate_skip_models"),
        }

    tag = model_tag or models[0]
    timeout = max(15.0, float(settings.llm_request_timeout))
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base}/api/generate",
                json={"model": tag, "prompt": "Say OK", "stream": False},
            )
            response.raise_for_status()
            payload = response.json()
        reply = (payload.get("response") or "")[:40]
        return {
            "id": "ollama_generate",
            "label": _label("ollama_generate"),
            "ok": True,
            "message": t("setup.generate_ok", model=tag, response=reply),
            "model": tag,
        }
    except Exception as exc:
        return {
            "id": "ollama_generate",
            "label": _label("ollama_generate"),
            "ok": False,
            "message": t("setup.generate_fail", model=tag, error=str(exc)),
            "model": tag,
        }


def _parse_ollama_tag(model_ref: str | None) -> str | None:
    """
    Extract an Ollama model tag from a LiteLLM model reference.

    :param model_ref: Model string such as ollama/llama3.1:8b
    :return: Ollama tag or None
    """
    if not model_ref:
        return None
    value = model_ref.strip()
    if value.startswith("ollama/"):
        return value[len("ollama/") :]
    if "/" not in value:
        return value
    return None


def test_default_model_available(
    default_model: str | None,
    installed: list[str],
) -> dict[str, Any]:
    """
    Verify the configured default model is available.

    :param default_model: LiteLLM default model reference
    :param installed: Tags reported by Ollama
    :return: Test result dict
    """
    provider = detect_provider_from_model(default_model)
    if provider is not None:
        if get_api_key(provider):
            return {
                "id": "default_model",
                "label": t("settings_test.labels.default_model"),
                "ok": True,
                "message": t(
                    "settings_test.default_model_cloud_ok",
                    model=default_model or "",
                    provider=provider.id.title(),
                ),
                "model": default_model,
            }
        return {
            "id": "default_model",
            "label": t("settings_test.labels.default_model"),
            "ok": False,
            "message": t(
                "settings_test.default_model_cloud_missing_key",
                model=default_model or "",
                provider=provider.id.title(),
            ),
            "model": default_model,
        }

    tag = _parse_ollama_tag(default_model)
    if not tag:
        return {
            "id": "default_model",
            "label": t("settings_test.labels.default_model"),
            "ok": None,
            "skipped": True,
            "message": t("settings_test.default_model_skip"),
        }
    if tag in installed:
        return {
            "id": "default_model",
            "label": t("settings_test.labels.default_model"),
            "ok": True,
            "message": t("settings_test.default_model_ok", model=tag),
            "model": tag,
        }
    return {
        "id": "default_model",
        "label": t("settings_test.labels.default_model"),
        "ok": False,
        "message": t("settings_test.default_model_missing", model=tag),
        "model": tag,
    }


def test_registry_on_ollama(installed: list[str]) -> dict[str, Any]:
    """
    Check whether registry models are available on the Ollama server.

    :param installed: Tags reported by Ollama
    :return: Test result dict
    """
    model_store.reload()
    models = [m for m in model_store.list_models() if m.get("enabled", True)]
    if not models:
        return {
            "id": "registry_ollama",
            "label": t("settings_test.labels.registry_ollama"),
            "ok": None,
            "skipped": True,
            "message": t("settings_test.registry_skip"),
        }

    installed_set = set(installed)
    missing = [m["ollama_tag"] for m in models if m.get("ollama_tag") not in installed_set]
    if not missing:
        return {
            "id": "registry_ollama",
            "label": t("settings_test.labels.registry_ollama"),
            "ok": True,
            "message": t("settings_test.registry_all_ok", count=len(models)),
            "count": len(models),
        }
    return {
        "id": "registry_ollama",
        "label": t("settings_test.labels.registry_ollama"),
        "ok": False,
        "warning": len(missing) < len(models),
        "message": t(
            "settings_test.registry_missing",
            missing=len(missing),
            total=len(models),
            models=", ".join(missing[:5]),
        ),
        "models": missing,
        "count": len(models),
    }


async def run_model_access_tests(
    ollama_url: str | None = None,
    default_model: str | None = None,
    openai_api_key: str | None = None,
    anthropic_api_key: str | None = None,
    gemini_api_key: str | None = None,
    groq_api_key: str | None = None,
    mistral_api_key: str | None = None,
    test_inference: bool = True,
) -> dict[str, Any]:
    """
    Run model access checks for the settings dialog.

    :param ollama_url: Optional Ollama URL override
    :param default_model: Optional default model override
    :param openai_api_key: Optional OpenAI key override for the test run
    :param anthropic_api_key: Optional Anthropic key override for the test run
    :param gemini_api_key: Optional Gemini key override for the test run
    :param groq_api_key: Optional Groq key override for the test run
    :param mistral_api_key: Optional Mistral key override for the test run
    :param test_inference: Whether to run a short inference test
    :return: Aggregated test report
    """
    base = (ollama_url or settings.ollama_base_url).rstrip("/")
    model_ref = default_model or settings.default_model
    key_overrides = {
        "openai_api_key": openai_api_key,
        "anthropic_api_key": anthropic_api_key,
        "gemini_api_key": gemini_api_key,
        "groq_api_key": groq_api_key,
        "mistral_api_key": mistral_api_key,
    }

    async with _temporary_cloud_keys(key_overrides):
        ollama_result = await test_ollama(base)
        installed = ollama_result.get("models") or []
        results = [
            ollama_result,
            test_default_model_available(model_ref, installed),
            test_registry_on_ollama(installed),
            *await test_all_cloud_providers(),
        ]

        if test_inference:
            if detect_provider_from_model(model_ref):
                results.append(await test_cloud_inference(model_ref))
            elif ollama_result.get("ok") and installed:
                tag = _parse_ollama_tag(model_ref)
                if tag and tag not in installed:
                    tag = installed[0]
                results.append(await test_ollama_generate(tag, base))

    required_ok = ollama_result.get("ok") is True
    optional_issues = [r for r in results if r.get("ok") is False or r.get("warning")]

    return {
        "all_required_ok": required_ok,
        "results": results,
        "summary": (
            t("settings_test.summary_ok")
            if required_ok and not optional_issues
            else t("settings_test.summary_partial")
            if required_ok
            else t("settings_test.summary_fail")
        ),
        "optional_issues": len(optional_issues),
    }


def test_model_registry() -> dict[str, Any]:
    """Check user model registry state."""
    model_store.reload()
    models = model_store.list_models()
    enabled = [m for m in models if m.get("enabled", True)]
    return {
        "id": "model_registry",
        "label": _label("model_registry"),
        "ok": len(enabled) > 0,
        "warning": len(models) > 0 and len(enabled) == 0,
        "message": (
            t("setup.registry_ok", count=len(enabled))
            if enabled
            else t("setup.registry_empty")
        ),
        "count": len(enabled),
    }


async def run_all_tests(
    ollama_url: str | None = None,
    workspace_path: str | None = None,
    test_generate: bool = True,
) -> dict[str, Any]:
    """
    Run complete setup test suite.

    :param ollama_url: Optional Ollama URL for tests
    :param workspace_path: Optional workspace path
    :param test_generate: Whether to run inference test
    :return: Aggregated test results
    """
    results = [
        await test_backend(),
        await test_ollama(ollama_url),
        test_workspace(workspace_path),
        test_model_registry(),
        *await test_all_cloud_providers(),
    ]
    if test_generate:
        results.append(await test_ollama_generate())

    required_ok = all(
        r.get("ok") is True
        for r in results
        if r["id"] in ("backend", "ollama", "workspace") and not r.get("skipped")
    )
    optional_issues = [
        r for r in results
        if r.get("ok") is False or r.get("warning")
    ]

    return {
        "all_required_ok": required_ok,
        "results": results,
        "summary": t("setup.summary_ok") if required_ok else t("setup.summary_fail"),
        "optional_issues": len(optional_issues),
    }
