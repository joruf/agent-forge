"""Lazy installation helpers for optional Python packages."""

from __future__ import annotations

import importlib
import subprocess
import sys


class OptionalDependencyError(RuntimeError):
    """Raised when an optional package cannot be imported or installed."""


def ensure_package(
    package_name: str,
    *,
    import_name: str | None = None,
    install: bool = True,
) -> None:
    """
    Import an optional package, installing it into the active venv when missing.

    :param package_name: PyPI package name (e.g. ``pypdf``)
    :param import_name: Module name for import (defaults to package_name)
    :param install: Whether pip install may be attempted
    :raises OptionalDependencyError: When import and optional install fail
    """
    module_name = import_name or package_name.replace("-", "_")
    try:
        importlib.import_module(module_name)
        return
    except ImportError:
        if not install:
            raise OptionalDependencyError(
                f"Optional dependency '{package_name}' is not installed.",
            ) from None

    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", package_name],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise OptionalDependencyError(
            f"Failed to install optional dependency '{package_name}': {detail}",
        )

    importlib.import_module(module_name)


def ensure_document_packages(*, install: bool = True) -> None:
    """
    Ensure PDF and Word document libraries are available.

    :param install: Whether pip install may be attempted for missing packages
    :raises OptionalDependencyError: When a required package remains unavailable
    """
    ensure_package("pypdf", install=install)
    ensure_package("python-docx", import_name="docx", install=install)
    ensure_package("fpdf2", import_name="fpdf", install=install)
