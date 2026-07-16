#!/usr/bin/env python3
"""Install AgentForge dependencies and desktop entry."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def install_system_deps() -> None:
    """Install Linux packages for Tauri and Chromium."""
    if not sys.platform.startswith("linux"):
        print("System dependency auto-install is available on Linux only.")
        print("Please install Python 3.12+ and Node.js 20+ manually on this OS.")
        return

    print("Installing system dependencies for native desktop (Tauri)...")
    if not shutil_which("apt-get"):
        print("Please install Python 3.12+, Node.js 20+, Chromium, and Tauri Linux deps manually.")
        return

    packages_41 = [
        "python3", "python3-venv", "python3-pip", "curl", "build-essential", "pkg-config",
        "chromium-browser", "libwebkit2gtk-4.1-dev", "libjavascriptcoregtk-4.1-dev",
        "libgtk-3-dev", "libayatana-appindicator3-dev", "librsvg2-dev", "patchelf",
    ]
    packages_40 = [
        "python3", "python3-venv", "python3-pip", "curl", "build-essential", "pkg-config",
        "chromium-browser", "libwebkit2gtk-4.0-dev", "libjavascriptcoregtk-4.0-dev",
        "libgtk-3-dev", "libappindicator3-dev", "librsvg2-dev", "patchelf",
    ]

    subprocess.run(["sudo", "apt-get", "update", "-qq"], check=False)
    result = subprocess.run(
        ["sudo", "apt-get", "install", "-y", "-qq", *packages_41],
        check=False,
    )
    if result.returncode != 0:
        subprocess.run(["sudo", "apt-get", "install", "-y", "-qq", *packages_40], check=False)
    print("System dependencies installed.")


def install_backend() -> None:
    """Create venv and install Python dependencies."""
    print("Setting up Python backend...")
    subprocess.run([sys.executable, "-m", "venv", str(BACKEND / ".venv")], cwd=BACKEND, check=True)
    venv_python = BACKEND / ".venv" / ("Scripts/python.exe" if sys.platform.startswith("win") else "bin/python")
    if not venv_python.exists():
        venv_python = BACKEND / ".venv" / ("Scripts/python" if sys.platform.startswith("win") else "bin/python")
    subprocess.run([str(venv_python), "-m", "pip", "install", "--upgrade", "pip", "-q"], check=True)
    subprocess.run([str(venv_python), "-m", "pip", "install", "-r", "requirements.txt", "-q"], cwd=BACKEND, check=True)
    print("Backend ready.")


def install_frontend() -> None:
    """Install npm dependencies."""
    print("Setting up frontend...")
    if not shutil_which("npm"):
        print("Node.js/npm not found. Install Node.js 20+ first.")
        sys.exit(1)
    subprocess.run(["npm", "install"], cwd=FRONTEND, check=True)
    print("Frontend ready.")


def install_desktop_hint() -> None:
    """Print Tauri build hint."""
    if shutil_which("cargo"):
        print("Rust found — Tauri desktop build available.")
        print("Run: cd frontend && npm run tauri:build")
    else:
        print("Rust not installed. For desktop app:")
        print("  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh")


def generate_launcher_icons() -> None:
    """Build PNG/ICO launcher assets from assets/icon.svg."""
    script = ROOT / "scripts" / "generate_icons.py"
    if not script.is_file():
        print("Icon generator missing; skipping launcher icons.")
        return
    try:
        subprocess.run([sys.executable, str(script)], cwd=ROOT, check=True)
    except subprocess.CalledProcessError:
        print("Launcher icon generation failed (ImageMagick convert required).")


def create_desktop_entry() -> None:
    """Create Linux desktop launcher via shared desktop setup."""
    if not sys.platform.startswith("linux"):
        print("Skipping desktop shortcut creation (Linux only).")
        return

    from desktop_setup import install_desktop_shortcut, mark_initialization_done

    success, path = install_desktop_shortcut()
    if success and path:
        print(f"Desktop shortcut created: {path}")
    else:
        print("Could not create desktop shortcut.")
    mark_initialization_done()


def shutil_which(name: str) -> bool:
    """Check whether a command exists."""
    from shutil import which
    return which(name) is not None


def main() -> None:
    """Run installation."""
    parser = argparse.ArgumentParser(description="Install AgentForge")
    parser.add_argument("--system", action="store_true", help="Install system packages")
    args = parser.parse_args()

    print("=== AgentForge Installation ===")
    if args.system:
        install_system_deps()
    install_backend()
    install_frontend()
    generate_launcher_icons()
    install_desktop_hint()
    create_desktop_entry()
    print("")
    print("=== Installation complete ===")
    start_cmd = "py -3" if sys.platform.startswith("win") else "python3"
    print(f"Start AgentForge: {start_cmd} {ROOT / 'run.py'}")
    print("Or open AgentForge from your application menu.")


if __name__ == "__main__":
    main()
