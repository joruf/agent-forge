"""Shared utilities for AgentForge launcher scripts."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
PID_DIR = ROOT / ".run"
LOG_DIR = ROOT / ".run" / "logs"
BACKEND_PORT = 8765
FRONTEND_PORT = 5173
BACKEND_HEALTH = f"http://127.0.0.1:{BACKEND_PORT}/api/health"
FRONTEND_URL = f"http://127.0.0.1:{FRONTEND_PORT}/"
PROD_APP_URL = f"http://127.0.0.1:{BACKEND_PORT}/"


def ensure_dirs() -> None:
    """Create runtime directories."""
    PID_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def python_executable() -> Path:
    """Return backend venv python if available."""
    venv_python = BACKEND / ".venv" / "bin" / "python"
    if venv_python.exists():
        return venv_python
    return Path(sys.executable)


def pids_on_port(port: int) -> list[int]:
    """Return process IDs listening on a TCP port."""
    if not shutil.which("lsof"):
        return []
    try:
        output = subprocess.check_output(
            ["lsof", "-ti", f":{port}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.CalledProcessError:
        return []
    return [int(pid) for pid in output.split() if pid.strip().isdigit()]


def free_port(port: int) -> None:
    """Stop processes occupying a port."""
    pids = pids_on_port(port)
    if not pids:
        return
    print(f"Port {port} belegt — beende alten Prozess...")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    time.sleep(1)
    for pid in pids_on_port(port):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.5)


def wait_for_http(url: str, retries: int = 40) -> None:
    """Wait until an HTTP endpoint responds."""
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Nicht erreichbar: {url}")


def http_ok(url: str) -> bool:
    """Check whether an HTTP URL is reachable."""
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status < 500
    except (urllib.error.URLError, TimeoutError):
        return False


def read_pid(name: str) -> int | None:
    """Read a stored PID file."""
    path = PID_DIR / f"{name}.pid"
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except ValueError:
        return None


def write_pid(name: str, pid: int) -> None:
    """Store a process ID."""
    ensure_dirs()
    (PID_DIR / f"{name}.pid").write_text(str(pid), encoding="utf-8")


def remove_pid(name: str) -> None:
    """Remove a PID file."""
    path = PID_DIR / f"{name}.pid"
    if path.exists():
        path.unlink()


def stop_pid(name: str) -> None:
    """Stop a process from a PID file."""
    pid = read_pid(name)
    if pid is None:
        return
    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Stopping {name} (PID {pid})...")
    except OSError:
        pass
    remove_pid(name)


def tauri_deps_ok() -> bool:
    """Check whether Tauri build dependencies are available."""
    if not shutil.which("pkg-config"):
        return False
    try:
        subprocess.run(
            ["pkg-config", "--exists", "javascriptcoregtk-4.1", "webkit2gtk-4.1"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def tauri_binary() -> Path | None:
    """Return compiled Tauri binary if present."""
    binary = FRONTEND / "src-tauri" / "target" / "debug" / "agentforge"
    if binary.exists() and os.access(binary, os.X_OK):
        return binary
    return None


def find_browser_command() -> list[str] | None:
    """Find a browser that supports app/window mode."""
    candidates = [
        [
            "chromium",
            "--app={url}",
            "--class=agentforge",
            "--name=AgentForge",
            "--disable-dev-shm-usage",
            "--disable-translate",
            "--lang=en-US",
        ],
        [
            "chromium-browser",
            "--app={url}",
            "--class=agentforge",
            "--name=AgentForge",
            "--disable-dev-shm-usage",
            "--disable-translate",
            "--lang=en-US",
        ],
        [
            "google-chrome",
            "--app={url}",
            "--class=agentforge",
            "--name=AgentForge",
            "--disable-translate",
            "--lang=en-US",
        ],
        [
            "microsoft-edge",
            "--app={url}",
            "--class=agentforge",
            "--name=AgentForge",
            "--disable-translate",
            "--lang=en-US",
        ],
        ["firefox", "--new-window", "{url}"],
    ]
    for candidate in candidates:
        if shutil.which(candidate[0]):
            return candidate
    return None
