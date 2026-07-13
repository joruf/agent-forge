#!/usr/bin/env python3
"""Run AgentForge backend unit tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV_PY = BACKEND / ".venv" / "bin" / "python"


def main() -> int:
    """Execute pytest for backend tests."""
    pytest = BACKEND / ".venv" / "bin" / "pytest"
    if not pytest.exists():
        print("Run install.py first to create the virtual environment.")
        return 1

    live = "--live" in sys.argv
    args = [str(pytest), "tests/", "-v"]
    if not live:
        args.extend(["--ignore=tests/test_live_ollama.py"])
    else:
        import os
        os.environ["AGENTFORGE_LIVE_TESTS"] = "1"

    return subprocess.call(args, cwd=BACKEND)


if __name__ == "__main__":
    sys.exit(main())
