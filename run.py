#!/usr/bin/env python3
"""Start AgentForge backend, UI server, and desktop window."""

from __future__ import annotations

import argparse
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
    FRONTEND,
    FRONTEND_URL,
    LOG_DIR,
    PROD_APP_URL,
    backend_health_ok,
    ensure_dirs,
    find_browser_command,
    free_port,
    http_ok,
    launcher_log,
    python_executable,
    read_pid,
    tauri_binary,
    tauri_deps_ok,
    wait_for_backend_ready,
    wait_for_http,
    write_pid,
)


def log_to_file(message: str) -> None:
    """Append launcher messages to the log file only."""
    launcher_log(message)


def log_message(message: str, *, console: bool = False) -> None:
    """Log to file and optionally print to console."""
    log_to_file(message)
    if console:
        print(message, flush=True)


def shutil_which(name: str) -> bool:
    """Check whether a command exists."""
    from shutil import which

    return which(name) is not None


def show_error(message: str) -> None:
    """Show an error dialog when no terminal is attached."""
    log_to_file(f"ERROR: {message}")
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


def _venv_python_path() -> Path:
    """Return the backend venv python executable path."""
    if sys.platform.startswith("win"):
        for name in ("Scripts/python.exe", "Scripts/python"):
            candidate = BACKEND / ".venv" / name
            if candidate.exists():
                return candidate
    return BACKEND / ".venv" / "bin" / "python"


def _pip_log_handle():
    """Open backend install log for pip output."""
    ensure_dirs()
    return open(LOG_DIR / "backend-install.log", "a", encoding="utf-8")


def ensure_backend_env() -> None:
    """Copy .env.example to backend/.env when missing."""
    env_file = BACKEND / ".env"
    example = ROOT / ".env.example"
    if env_file.exists() or not example.exists():
        return
    log_to_file("Copying .env.example -> backend/.env")
    shutil.copy(example, env_file)


def ensure_backend() -> None:
    """Create backend venv and install Python dependencies when missing."""
    ensure_backend_env()
    venv = BACKEND / ".venv"
    venv_python = _venv_python_path()

    if venv.exists() and venv_python.exists():
        try:
            subprocess.run(
                [str(venv_python), "-c", "import agentforge"],
                cwd=BACKEND,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return
        except subprocess.CalledProcessError:
            log_to_file("Backend venv incomplete — reinstalling dependencies...")

    if venv.exists() and not venv_python.exists():
        log_to_file("Backend virtualenv is broken — recreating...")
        shutil.rmtree(venv, ignore_errors=True)

    log_to_file("Creating backend virtualenv...")
    with _pip_log_handle() as log_file:
        subprocess.run(
            [sys.executable, "-m", "venv", str(venv)],
            cwd=BACKEND,
            check=True,
            stdout=log_file,
            stderr=log_file,
        )

    venv_python = _venv_python_path()
    if not venv_python.exists():
        raise RuntimeError("Backend virtualenv python not found after creation.")

    log_to_file("Installing backend dependencies (pip)...")
    with _pip_log_handle() as log_file:
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"],
            cwd=BACKEND,
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
        subprocess.run(
            [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "-q"],
            cwd=BACKEND,
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
    log_to_file("Backend dependencies ready.")


def ensure_frontend_deps() -> None:
    """Install npm dependencies when node_modules is missing."""
    if (FRONTEND / "node_modules").exists():
        return

    if not shutil_which("npm"):
        raise RuntimeError("npm not found. Install Node.js 20+ first.")

    log_to_file("Installing frontend dependencies (npm install)...")
    ensure_dirs()
    with open(LOG_DIR / "frontend-install.log", "a", encoding="utf-8") as log_file:
        subprocess.run(
            ["npm", "install", "--silent"],
            cwd=FRONTEND,
            check=True,
            stdout=log_file,
            stderr=log_file,
        )
    log_to_file("Frontend dependencies ready.")


def start_backend(*, production: bool = False) -> None:
    """Start FastAPI backend if not already running."""
    if backend_health_ok():
        log_to_file("Backend läuft bereits.")
        return

    ensure_backend()
    free_port(8765)
    ensure_dirs()
    log_file = open(LOG_DIR / "backend.log", "a", encoding="utf-8")
    log_to_file("Starte Backend...")
    env = os.environ.copy()
    if production:
        env["AGENTFORGE_PROD"] = "1"
    process = subprocess.Popen(
        [str(python_executable()), "-m", "agentforge"],
        cwd=BACKEND,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
        env=env,
    )
    write_pid("backend", process.pid)
    wait_for_backend_ready()
    log_to_file("Backend bereit.")


def clear_vite_cache() -> None:
    """Remove Vite transform cache to avoid stale empty CSS/TS module payloads."""
    cache_dir = FRONTEND / "node_modules" / ".vite"
    if cache_dir.exists():
        shutil.rmtree(cache_dir)


def start_vite() -> None:
    """Start Vite dev server if not already running."""
    if http_ok(FRONTEND_URL):
        log_to_file("Frontend läuft bereits.")
        return

    ensure_frontend_deps()
    free_port(5173)
    clear_vite_cache()
    ensure_dirs()
    log_file = open(LOG_DIR / "frontend.log", "a", encoding="utf-8")
    log_to_file("Starte UI-Server...")
    process = subprocess.Popen(
        ["npm", "run", "dev"],
        cwd=FRONTEND,
        stdout=log_file,
        stderr=log_file,
        start_new_session=True,
    )
    write_pid("frontend", process.pid)
    wait_for_http(FRONTEND_URL)
    log_to_file("UI-Server bereit.")


def start_tauri() -> None:
    """Start Tauri development mode."""
    log_to_file("Starte native Desktop-App (Tauri)...")
    log_to_file("Hinweis: Erster Start kompiliert Rust — kann einige Minuten dauern.")
    subprocess.run(["npm", "run", "tauri:dev"], cwd=FRONTEND, check=True)


def verify_backend_before_ui() -> None:
    """Re-check backend health before opening the UI."""
    if backend_health_ok():
        return
    log_to_file("Backend noch nicht bereit — warte...")
    wait_for_backend_ready()


def open_app_window() -> None:
    """Open AgentForge in a standalone browser window."""
    verify_backend_before_ui()
    browser = find_browser_command()
    if browser is None:
        open_browser()
        return

    log_to_file("Öffne AgentForge als Desktop-Fenster...")
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
    verify_backend_before_ui()
    log_to_file(f"Browser: {FRONTEND_URL}")
    if shutil_which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", FRONTEND_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    wait_for_frontend()


def ensure_frontend_build() -> None:
    """Build the frontend for production serving when dist is missing."""
    dist_index = FRONTEND / "dist" / "index.html"
    if dist_index.exists():
        return

    ensure_frontend_deps()
    log_to_file("Baue Frontend für Produktion (npm run build)...")
    env = os.environ.copy()
    env["VITE_API_BASE"] = "/api"
    with open(LOG_DIR / "frontend-build.log", "a", encoding="utf-8") as log_file:
        subprocess.run(
            ["npm", "run", "build"],
            cwd=FRONTEND,
            check=True,
            env=env,
            stdout=log_file,
            stderr=log_file,
        )


def run_production() -> None:
    """Start backend with static frontend build on a single port."""
    log_to_file("=== AgentForge (Produktion) ===")
    ensure_frontend_build()
    try:
        start_backend(production=True)
    except Exception as exc:
        show_error(f"AgentForge konnte nicht starten:\n{exc}")
        log_to_file(traceback.format_exc())
        sys.exit(1)

    log_to_file(f"Produktion: {PROD_APP_URL}")
    if shutil_which("xdg-open"):
        subprocess.Popen(
            ["xdg-open", PROD_APP_URL],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    pid = read_pid("backend")
    if pid is None:
        while True:
            time.sleep(3600)
        return
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        while True:
            time.sleep(3600)


def parse_args() -> argparse.Namespace:
    """Parse launcher CLI arguments."""
    parser = argparse.ArgumentParser(description="Start AgentForge")
    parser.add_argument(
        "--prod",
        action="store_true",
        help="Production mode: serve built frontend from backend (port 8765, no Vite dev server)",
    )
    return parser.parse_args()


def main() -> None:
    """Run AgentForge launcher."""
    args = parse_args()
    os.chdir(ROOT)
    ensure_dirs()
    os.environ.setdefault("AGENTFORGE_SKIP_DESKTOP_SETUP", "1")

    if args.prod:
        run_production()
        return

    mode = os.environ.get("AGENTFORGE_MODE", "auto")
    log_to_file("=== AgentForge ===")

    try:
        start_backend()
        start_vite()
    except Exception as exc:
        show_error(f"AgentForge konnte nicht starten:\n{exc}")
        log_to_file(traceback.format_exc())
        sys.exit(1)

    verify_backend_before_ui()

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
        log_to_file("Starte kompilierte Tauri-App...")
        subprocess.run([str(binary)], check=True)
        return

    if tauri_deps_ok() and shutil_which("cargo"):
        start_tauri()
        return

    if find_browser_command() is not None:
        open_app_window()
        return

    open_browser()


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        show_error(f"AgentForge Fehler:\n{exc}")
        log_to_file(traceback.format_exc())
        sys.exit(1)
