#!/usr/bin/env python3
"""Stop running AgentForge processes."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from launcher_common import free_port, stop_pid  # noqa: E402


def main() -> None:
    """Stop backend, frontend, and free ports."""
    stop_pid("backend")
    stop_pid("frontend")
    free_port(8765)
    free_port(5173)
    print("AgentForge stopped.")


if __name__ == "__main__":
    main()
