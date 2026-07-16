#!/usr/bin/env python3
"""Generate PNG and ICO launcher icons from assets/icon.svg."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SVG = ROOT / "assets" / "icon.svg"
ASSETS_ICONS = ROOT / "assets" / "icons"
PUBLIC = ROOT / "frontend" / "public"
TAURI_ICONS = ROOT / "frontend" / "src-tauri" / "icons"

PNG_TARGETS: dict[Path, list[int]] = {
    ASSETS_ICONS: [16, 32, 48, 64, 128, 256],
    PUBLIC: [16, 32, 180, 192, 512],
    TAURI_ICONS: [32, 128, 256],
}


def _convert(size: int, target: Path) -> None:
    """
    Render the SVG source icon to one PNG file.

    :param size: Output width and height in pixels
    :param target: Destination PNG path
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "convert",
        "-background",
        "none",
        str(SVG),
        "-resize",
        f"{size}x{size}",
        str(target),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write_favicon_ico() -> None:
    """Build a multi-size favicon.ico for browsers and launchers."""
    sizes = [16, 32, 48]
    temp_files: list[Path] = []
    for size in sizes:
        temp_path = PUBLIC / f".favicon-{size}.png"
        _convert(size, temp_path)
        temp_files.append(temp_path)
    ico_path = PUBLIC / "favicon.ico"
    subprocess.run(
        ["convert", *[str(path) for path in temp_files], str(ico_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for temp_path in temp_files:
        temp_path.unlink(missing_ok=True)


def generate_icons() -> None:
    """Generate all PNG/ICO assets required by web, desktop, and Tauri launchers."""
    if not SVG.is_file():
        raise FileNotFoundError(f"Missing source icon: {SVG}")
    if shutil.which("convert") is None:
        raise RuntimeError("ImageMagick 'convert' is required to generate icons.")

    for directory, sizes in PNG_TARGETS.items():
        for size in sizes:
            if directory == TAURI_ICONS and size == 256:
                _convert(size, directory / "128x128@2x.png")
                continue
            if directory == TAURI_ICONS and size == 32:
                _convert(size, directory / "32x32.png")
                continue
            if directory == TAURI_ICONS and size == 128:
                _convert(size, directory / "128x128.png")
                _convert(size, directory / "icon.png")
                continue
            if directory == PUBLIC and size == 16:
                _convert(size, directory / "favicon-16x16.png")
                continue
            if directory == PUBLIC and size == 32:
                _convert(size, directory / "favicon-32x32.png")
                continue
            if directory == PUBLIC and size == 180:
                _convert(size, directory / "apple-touch-icon.png")
                continue
            if directory == PUBLIC and size == 192:
                _convert(size, directory / "icon-192.png")
                continue
            if directory == PUBLIC and size == 512:
                _convert(size, directory / "icon-512.png")
                continue
            _convert(size, directory / f"{size}x{size}.png")

    _convert(256, ASSETS_ICONS / "agentforge.png")
    _write_favicon_ico()
    _convert(256, TAURI_ICONS / "icon.ico")  # ImageMagick accepts ICO output


def main() -> int:
    """
    CLI entry point.

    :return: Process exit code
    """
    try:
        generate_icons()
    except (FileNotFoundError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Icon generation failed: {exc}", file=sys.stderr)
        return 1
    print("Generated AgentForge launcher icons.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
