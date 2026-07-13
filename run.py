#!/usr/bin/env python3
"""Start AgentForge backend, UI server, and desktop window."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from launcher_common import (  # noqa: E402
    BACKEND,
    BACKEND_HEALTH,
    FRONTEND,
    FRONTEND_URL,
    LOG_DIR,
    ensure_dirs,
    find_browser_command,
    free_port,
    http_ok,
    python_executable,
    read_pid,
    tauri_binary,
    tauri_deps_ok,
    wait_for_http,
    write_pid,
)


def log_message(message: str) -> None:
    """Print and append launcher messages to the log file."""
    print(message, flush=True)
    ensure_dirs()
    with open(LOG_DIR / "launcher.log", "a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def shutil_which(name: str) -> bool:
    """Check whether a command exists."""
    from shutil import which

    return which(name) is not None


def show_error(message: str) -> None:
    """Show an error dialog when no terminal is attached."""
    log_message(f"ERROR: {message}")
    if not os.environ.get("DISPLAY"):
        return
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("AgentForge", message, parent=root)
        root.destroy()
        return
    except Exception:
        pass
    if shutil_which("zenity"):
        subprocess.run(
            ["zenity", "--error", "--title=AgentForge", "--text", message, "--width=420"],
            check=False,
        )


def start_backend() -> None:
    """Start FastAPI backend if not already running."""
    if http_ok(BACKEND_HEALTH):
        log_message("Backend läuft bereits.")
        return

    if not (BACKEND / ".venv").exists():
        message = "Backend nicht installiert. Bitte zuerst: python3 install.py"
        show_error(message)
        sys.exit(1)

    free_port(8765)
    ensure_dirs()
    log_file = open(LOG_DIR / "backend.log", "a", encoding="utf-8")
    log_message("Starte Backend...")
    process = subprocess.Popen(
        [str(python_executable()), "-m", "agentforge"],
        cwd=BACKEND,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    write_pid("backend", process.pid)
    wait_for_http(BACKEND_HEALTH)
    log_message("Backend bereit.")


def clear_vite_cache() -> None:
    """Remove Vite transform cache to avoid stale empty CSS/TS module payloads."""
    cache_dir = FRONTEND / "node_modules" / ".vite"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


def start_vite() -> None:
    """Start Vite dev server if not already running."""
    if http_ok(FRONTEND_URL):
        log_message("Frontend läuft bereits.")
        return

    if not (FRONTEND / "node_modules").exists():
        message = "Frontend nicht installiert. Bitte zuerst: python3 install.py"
        show_error(message)
        sys.exit(1)

    free_port(5173)
    clear_vite_cache()
    ensure_dirs()
    log_file = open(LOG_DIR / "frontend.log", "a", encoding="utf-8")
    log_message("Starte UI-Server...")
    process = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=FRONTEND,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    write_pid("frontend", process.pid)
    wait_for_http(FRONTEND_URL)
    log_message("UI-Server bereit.")


def start_tauri() -> None:
    """Start Tauri development mode."""
    log_message("Starte native Desktop-App (Tauri)...")
    log_message("Hinweis: Erster Start kompiliert Rust — kann einige Minuten dauern.")
    subprocess.run(["npm", "run", "tauri:dev"], cwd=FRONTEND, check=True)


def open_app_window() -> None:
    """Open AgentForge in a standalone browser window."""
    browser = find_browser_command()
    if browser is None:
        message = "Kein Chromium/Firefox gefunden. Installiere: sudo apt install chromium-browser"
        show_error(message)
        sys.exit(1)

    log_message("Öffne AgentForge als Desktop-Fenster...")
    command = [part.format(url=FRONTEND_URL) for part in browser]
    subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    wait_for_frontend()


def wait_for_frontend() -> None:
    """Keep launcher alive while frontend process runs."""
    pid = read_pid("frontend")
    if pid is None:
        while True:
            time.sleep(3600)
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        while True:
            time.sleep(3600)


def open_browser() -> None:
    """Open AgentForge in the default browser."""
    log_message(f"Browser: {FRONTEND_URL}")
    if shutil_which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", FRONTEND_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    wait_for_frontend()


def main() -> None:
    """Run AgentForge launcher."""
    os.chdir(ROOT)
    ensure_dirs()

    try:
        from desktop_setup import maybe_prompt_desktop_setup

        maybe_prompt_desktop_setup()
    except Exception as exc:
        log_message(f"Desktop setup skipped: {exc}")

    mode = os.environ.get("AGENTFORGE_MODE", "auto")
    log_message("=== AgentForge ===")

    try:
        start_backend()
        start_vite()
    except Exception as exc:
        show_error(f"AgentForge konnte nicht starten:\n{exc}")
        log_message(traceback.format_exc())
        sys.exit(1)

    if mode == "tauri":
        start_tauri()
        return

    if mode == "browser":
        open_browser()
        return

    if mode == "window":
        open_app_window()
        return

    binary = tauri_binary()
    if binary is not None:
        log_message("Starte kompilierte Tauri-App...")
        subprocess.run([str(binary)], check=True)
        return

    if tauri_deps_ok() and shutil_which("cargo"):
        start_tauri()
        return

    if find_browser_command() is not None:
        open_app_window()
        return

    log_message("")
    log_message("Desktop-Fenster nicht verfügbar. Optionen:")
    log_message("  1) System-Pakete:  python3 install.py --system")
    log_message("  2) Chromium:       sudo apt install chromium-browser")
    log_message("  3) Nur Browser:    AGENTFORGE_MODE=browser python3 run.py")
    log_message("")
    log_message(f"Öffne {FRONTEND_URL} im Browser...")
    open_browser()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        show_error(f"AgentForge Fehler:\n{exc}")
        log_message(traceback.format_exc())
        sys.exit(1)
