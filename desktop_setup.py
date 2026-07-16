#!/usr/bin/env python3
"""First-run desktop shortcut setup for AgentForge."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from shutil import which

SCRIPT_DIR = Path(__file__).resolve().parent
INIT_FILE = SCRIPT_DIR / ".initialized"
RUN_SCRIPT = SCRIPT_DIR / "run.py"
DESKTOP_TEMPLATE = SCRIPT_DIR / "assets" / "AgentForge.desktop"
DESKTOP_FILENAME = "AgentForge.desktop"
ICON_FILE = SCRIPT_DIR / "assets" / "icons" / "agentforge.png"
ICON_THEME_NAME = "agentforge"
ICON_THEME_DIR = Path.home() / ".local" / "share" / "icons" / "hicolor"


def user_desktop_dir() -> Path:
    """
    Return the user's desktop directory.

    Reads XDG user-dirs when available and falls back to Desktop or Schreibtisch.

    :return: Desktop directory path
    """
    config = Path.home() / ".config" / "user-dirs.dirs"
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("XDG_DESKTOP_DIR="):
                value = line.split("=", 1)[1].strip().strip('"')
                if value.startswith("$HOME/"):
                    return Path.home() / value[len("$HOME/") :]
                if value == "$HOME":
                    return Path.home()
                return Path(value).expanduser()

    for name in ("Desktop", "Schreibtisch"):
        desktop = Path.home() / name
        if desktop.is_dir():
            return desktop

    return Path.home() / "Desktop"


def applications_dir() -> Path:
    """Return the user applications directory for the app menu."""
    return Path.home() / ".local" / "share" / "applications"


def build_desktop_entry_content() -> str:
    """
    Build the .desktop file contents with absolute paths to run.py.

    :return: Desktop entry file content
    """
    python_cmd = os.environ.get("AGENTFORGE_PYTHON", "python3")
    exec_line = f"Exec={python_cmd} {RUN_SCRIPT}\n"
    path_line = f"Path={SCRIPT_DIR}\n"
    icon_line = (
        f"Icon={ICON_FILE.resolve()}\n"
        if ICON_FILE.is_file()
        else "Icon=utilities-terminal\n"
    )

    if DESKTOP_TEMPLATE.is_file():
        lines: list[str] = []
        has_path = False
        for line in DESKTOP_TEMPLATE.read_text(encoding="utf-8").splitlines():
            if line.startswith("Exec="):
                lines.append(exec_line.rstrip())
            elif line.startswith("Path="):
                lines.append(path_line.rstrip())
                has_path = True
            elif line.startswith("Icon="):
                lines.append(icon_line.rstrip())
            else:
                lines.append(line)
        if not has_path:
            lines.insert(4, path_line.rstrip())
        return "\n".join(lines) + "\n"

    return (
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=AgentForge\n"
        "Comment=Multi-agent AI platform for coding and collaboration\n"
        f"{icon_line.rstrip()}\n"
        f"{exec_line.rstrip()}\n"
        f"{path_line.rstrip()}\n"
        "Terminal=false\n"
        "Categories=Development;Utility;\n"
        "StartupNotify=true\n"
        "StartupWMClass=AgentForge\n"
    )


def _write_shortcut(target: Path) -> None:
    """Write desktop entry and mark it executable."""
    target.write_text(build_desktop_entry_content(), encoding="utf-8")
    target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def install_icon_theme() -> None:
    """
    Install PNG icons into the user icon theme for launchers and taskbars.

    Many Linux desktops render SVG taskbar icons as a black square; PNG hicolor
    entries avoid that and match StartupWMClass=agentforge.
    """
    source_dir = SCRIPT_DIR / "assets" / "icons"
    if not source_dir.is_dir():
        return
    mappings: list[tuple[str, str]] = [
        ("16x16.png", "16x16"),
        ("32x32.png", "32x32"),
        ("48x48.png", "48x48"),
        ("64x64.png", "64x64"),
        ("128x128.png", "128x128"),
        ("agentforge.png", "256x256"),
    ]
    for filename, size in mappings:
        source = source_dir / filename
        if not source.is_file():
            continue
        target_dir = ICON_THEME_DIR / size / "apps"
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"{ICON_THEME_NAME}.png"
        target_path.write_bytes(source.read_bytes())

    index_theme = source_dir / "index.theme"
    if index_theme.is_file():
        ICON_THEME_DIR.mkdir(parents=True, exist_ok=True)
        (ICON_THEME_DIR / "index.theme").write_bytes(index_theme.read_bytes())

    cache_binary = which("gtk-update-icon-cache")
    if cache_binary and ICON_THEME_DIR.is_dir():
        subprocess.run(
            [cache_binary, "-f", "-t", str(ICON_THEME_DIR)],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    xdg_icon = which("xdg-icon-resource")
    icon_256 = source_dir / "agentforge.png"
    if xdg_icon and icon_256.is_file():
        subprocess.run(
            [
                xdg_icon,
                "install",
                "--context",
                "apps",
                "--size",
                "256",
                str(icon_256),
                ICON_THEME_NAME,
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def install_desktop_shortcut() -> tuple[bool, Path | None]:
    """
    Install shortcuts on the desktop and in the application menu.

    :return: Success flag and desktop shortcut path
    """
    try:
        install_icon_theme()
        desktop_dir = user_desktop_dir()
        desktop_dir.mkdir(parents=True, exist_ok=True)
        shortcut_path = desktop_dir / DESKTOP_FILENAME
        _write_shortcut(shortcut_path)

        menu_dir = applications_dir()
        menu_dir.mkdir(parents=True, exist_ok=True)
        _write_shortcut(menu_dir / DESKTOP_FILENAME)
        return True, shortcut_path
    except OSError:
        return False, None


def mark_initialization_done() -> None:
    """Create marker file so the first-run prompt is shown only once."""
    try:
        INIT_FILE.touch()
    except OSError:
        pass


def _ui_lang() -> str:
    """Return de or en based on environment."""
    lang = os.environ.get("LANG", "en").lower()
    return "de" if lang.startswith("de") else "en"


def maybe_prompt_desktop_setup() -> None:
    """Ask once on first run whether to create a desktop shortcut."""
    if INIT_FILE.exists():
        return

    lang = _ui_lang()
    title = "AgentForge" if lang == "en" else "AgentForge"
    question = (
        "Create a desktop shortcut for AgentForge?\n\n"
        "The shortcut will start AgentForge from:\n"
        f"{RUN_SCRIPT}"
        if lang == "en"
        else (
            "Desktop-Verknüpfung für AgentForge erstellen?\n\n"
            "Die Verknüpfung startet AgentForge über:\n"
            f"{RUN_SCRIPT}"
        )
    )
    error_text = (
        "Could not create the desktop shortcut."
        if lang == "en"
        else "Desktop-Verknüpfung konnte nicht erstellt werden."
    )

    answer = False
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        answer = messagebox.askyesno(title, question, parent=root)
        root.destroy()
    except Exception:
        if os.environ.get("DISPLAY") and which("zenity"):
            import subprocess

            result = subprocess.run(
                ["zenity", "--question", "--title=AgentForge", "--text", question, "--width=420"],
                check=False,
            )
            answer = result.returncode == 0
        else:
            mark_initialization_done()
            return

    if answer:
        success, _ = install_desktop_shortcut()
        if not success:
            try:
                import tkinter as tk
                from tkinter import messagebox

                root = tk.Tk()
                root.withdraw()
                messagebox.showerror(title, error_text, parent=root)
                root.destroy()
            except Exception:
                pass

    mark_initialization_done()
