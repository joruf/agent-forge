"""Tests for safe desktop/icon installation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import desktop_setup  # noqa: E402


@pytest.fixture()
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point icon installation at an isolated temporary home."""
    hicolor = tmp_path / ".local" / "share" / "icons" / "hicolor"
    monkeypatch.setattr(desktop_setup, "ICON_THEME_DIR", hicolor)
    monkeypatch.setattr(desktop_setup, "which", lambda _name: None)
    return tmp_path


def test_install_icon_theme_writes_pngs_without_creating_index(fake_home: Path) -> None:
    """AgentForge must install launcher PNGs but never create index.theme."""
    desktop_setup.install_icon_theme()

    hicolor = desktop_setup.ICON_THEME_DIR
    assert (hicolor / "48x48" / "apps" / "agentforge.png").is_file()
    assert (hicolor / "256x256" / "apps" / "agentforge.png").is_file()
    assert not (hicolor / "index.theme").exists()


def test_install_icon_theme_preserves_existing_index(fake_home: Path) -> None:
    """An existing user hicolor index.theme must remain unchanged."""
    hicolor = desktop_setup.ICON_THEME_DIR
    index_path = hicolor / "index.theme"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        "[Icon Theme]\n"
        "Name=Hicolor\n"
        "Comment=Existing user fallback\n"
        "Hidden=true\n"
        "\n"
        "Directories=scalable/apps,scalable/actions,symbolic/apps\n"
    )
    index_path.write_text(original, encoding="utf-8")

    desktop_setup.install_icon_theme()

    assert index_path.read_text(encoding="utf-8") == original
    assert (hicolor / "32x32" / "apps" / "agentforge.png").is_file()


def test_no_bundled_hicolor_index_theme_asset() -> None:
    """Regression guard: never ship a user-hicolor index.theme in assets."""
    assert not (ROOT / "assets" / "icons" / "index.theme").exists()


def test_desktop_entry_uses_absolute_icon_path() -> None:
    """Launcher must embed an absolute Icon= path so it works without theme metadata."""
    content = desktop_setup.build_desktop_entry_content()
    icon_line = next(line for line in content.splitlines() if line.startswith("Icon="))
    icon_value = icon_line.split("=", 1)[1]
    assert icon_value.startswith("/")
    assert icon_value.endswith("agentforge.png")
    assert Path(icon_value).is_file()


def test_install_desktop_shortcut_keeps_index_intact(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Full shortcut install must not replace an existing hicolor index.theme."""
    desktop = fake_home / "Schreibtisch"
    apps = fake_home / ".local" / "share" / "applications"
    desktop.mkdir(parents=True)
    apps.mkdir(parents=True)

    hicolor = desktop_setup.ICON_THEME_DIR
    index_path = hicolor / "index.theme"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    original = "[Icon Theme]\nName=Hicolor\nDirectories=scalable/actions\n"
    index_path.write_text(original, encoding="utf-8")

    monkeypatch.setattr(desktop_setup, "user_desktop_dir", lambda: desktop)
    monkeypatch.setattr(desktop_setup, "applications_dir", lambda: apps)

    success, path = desktop_setup.install_desktop_shortcut()

    assert success is True
    assert path == desktop / "AgentForge.desktop"
    assert path.is_file()
    assert (apps / "AgentForge.desktop").is_file()
    assert index_path.read_text(encoding="utf-8") == original
    shortcut = path.read_text(encoding="utf-8")
    assert any(line.startswith("Icon=/") for line in shortcut.splitlines())
