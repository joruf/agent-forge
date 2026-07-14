"""Compatibility helpers for LiteLLM package layout issues."""

from __future__ import annotations

import sys
import types
from pathlib import Path


def ensure_litellm_proxy_package() -> None:
    """
    Register litellm.llms.litellm_proxy when LiteLLM ships without a root __init__.py.

    Some LiteLLM releases include litellm_proxy/skills/__init__.py but no package
    initializer at litellm_proxy/__init__.py. Python then fails with
    ``No module named 'litellm.llms.litellm_proxy'`` during provider resolution.
    """
    package_name = "litellm.llms.litellm_proxy"
    if package_name in sys.modules:
        return

    try:
        import litellm
    except ImportError:
        return

    package_root = Path(litellm.__file__).resolve().parent / "llms" / "litellm_proxy"
    if not package_root.is_dir():
        return

    module = types.ModuleType(package_name)
    module.__path__ = [str(package_root)]  # type: ignore[attr-defined]
    sys.modules[package_name] = module
