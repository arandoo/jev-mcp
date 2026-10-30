"""Local image loading for Jev assets.

The server reads image files itself so the caller passes paths, not base64:
an MCP client would otherwise push every image's bytes through its own context.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

MAX_IMAGE_BYTES = 16 * 1024 * 1024
# Not in the OpenAPI spec: the API answers "Invalid image dimensions" (400) above
# this longest side. Probed 2026-10-30 on jev-1.14.0: 1568 ok, 1569 rejected.
MAX_IMAGE_SIDE = 1568
# Upper bound for files we are willing to read and downscale.
MAX_SOURCE_BYTES = 128 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


class ImageError(ValueError):
    """Raised with an actionable message when a file can't be sent as an image."""


def sniff_format(head: bytes) -> str | None:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    return None


def fit_longest_side(data: bytes, fmt: str) -> bytes:
    """Downscale so the longest side is at most MAX_IMAGE_SIDE; small images pass through byte-for-byte.

    Box answers are fractions of width/height, so they still map onto the original file.
    """
    try:
        with Image.open(io.BytesIO(data)) as im:
            if max(im.size) <= MAX_IMAGE_SIDE:
                return data
            im = ImageOps.exif_transpose(im)
            im.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.LANCZOS)
            out = io.BytesIO()
            if fmt == "jpeg":
                im.convert("RGB").save(out, format="JPEG", quality=90)
            else:
                im.save(out, format="PNG")
            return out.getvalue()
    except (OSError, ValueError, Image.DecompressionBombError) as e:
        raise ImageError(f"cannot decode image ({e})") from e


def load_image(path: str | Path) -> dict[str, Any]:
    """Return a TypeSafe image asset for a local PNG/JPEG file, downscaled to Jev's size limit if needed."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise ImageError(f"{p}: not a file.")
    size = p.stat().st_size
    if size > MAX_SOURCE_BYTES:
        raise ImageError(f"{p}: {size / 1_048_576:.0f} MB is too large to read; downscale it first.")
    try:
        data = p.read_bytes()
    except OSError as e:
        raise ImageError(f"{p}: cannot read file ({e.strerror or e}).") from e
    fmt = sniff_format(data[:8])
    if fmt is None:
        raise ImageError(
            f"{p}: Jev accepts only PNG or JPEG. Convert it first "
            f"(macOS: sips -s format png in.webp --out out.png; video: ffmpeg -i in.mp4 -vf fps=1 f_%04d.jpg)."
        )
    try:
        data = fit_longest_side(data, fmt)
    except ImageError as e:
        raise ImageError(f"{p}: {e}") from e
    if len(data) > MAX_IMAGE_BYTES:
        raise ImageError(f"{p}: {len(data) / 1_048_576:.1f} MB is over Jev's 16 MB image limit even after downscaling.")
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
