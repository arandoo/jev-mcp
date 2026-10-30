"""Local image loading for Jev assets.

The server reads image files itself so the caller passes paths, not base64:
an MCP client would otherwise push every image's bytes through its own context.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

MAX_IMAGE_BYTES = 16 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


class ImageError(ValueError):
    """Raised with an actionable message when a file can't be sent as an image."""


def sniff_format(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def load_image(path: str | Path) -> dict[str, Any]:
    """Return a TypeSafe image asset for a local PNG/JPEG file."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ImageError(f"{p}: not a file.")
    size = p.stat().st_size
    if size > MAX_IMAGE_BYTES:
        raise ImageError(f"{p}: {size / 1_048_576:.1f} MB is over Jev's 16 MB image limit; downscale it first.")
    data = p.read_bytes()
    fmt = sniff_format(data[:8])
    if fmt is None:
        raise ImageError(
            f"{p}: Jev accepts only PNG or JPEG. Convert it first "
            f"(macOS: sips -s format png in.webp --out out.png; video: ffmpeg -i in.mp4 -vf fps=1 f_%04d.jpg)."
        )
    return {"type": "image", "format": fmt, "data_base64": base64.b64encode(data).decode("ascii")}


def expand_paths(paths: list[str]) -> list[Path]:
    """Files stay as given; a directory contributes its PNG/JPEG files (not recursive), sorted."""
    out: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            out.extend(sorted(c for c in p.iterdir() if c.is_file() and c.suffix.lower() in IMAGE_SUFFIXES))
        else:
            out.append(p)
    return out
