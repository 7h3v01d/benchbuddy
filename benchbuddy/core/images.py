"""Checks for image files we're handed (part photos, OCR input) before anything parses them."""

from __future__ import annotations

from pathlib import Path

from .validation import DomainError

MAX_FILE_BYTES = 25 * 1024 * 1024        # a phone photo is a few MB; 25 MB leaves lots of room
MAX_PIXELS = 40_000_000                  # 40 MP: bigger than any phone camera, far below a "bomb"

_SIGNATURES = (
    (b"\xff\xd8\xff", ".jpg"),
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"BM", ".bmp"),
)


class ImageError(DomainError):
    pass


def sniff(path: str | Path) -> str:
    """Return the real image type's extension (from the file's bytes, not its name)."""
    p = Path(path)
    if not p.is_file():
        raise ImageError(f"photo not found: {p}")
    size = p.stat().st_size
    if size == 0:
        raise ImageError("that file is empty")
    if size > MAX_FILE_BYTES:
        raise ImageError(f"that file is {size / 1e6:.0f} MB: photos are limited to {MAX_FILE_BYTES // 1_000_000} MB")
    with open(p, "rb") as fh:
        head = fh.read(16)
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    for sig, ext in _SIGNATURES:
        if head.startswith(sig):
            return ext
    raise ImageError("that file isn't a JPEG, PNG, GIF, BMP or WebP image (whatever its name says)")


def check_image(path: str | Path) -> str:
    """Full check: size, real type, and (when Pillow is installed) decodable with sane dimensions.

    Returns the extension for the real type.
    """
    ext = sniff(path)
    try:
        from PIL import Image
    except ImportError:          # no Pillow: the signature check above is what we can do
        return ext
    try:
        with Image.open(path) as img:
            w, h = img.size
            if w * h > MAX_PIXELS:
                raise ImageError(f"that image is {w}×{h} ({w * h / 1e6:.0f} MP): the limit is "
                                 f"{MAX_PIXELS // 1_000_000} MP")
            img.verify()         # structural check without decoding all the pixels
    except ImageError:
        raise
    except Image.DecompressionBombError:
        raise ImageError(f"that image claims an enormous size (over {MAX_PIXELS // 1_000_000} MP): "
                         "refusing to open it") from None
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types for broken files
        raise ImageError(f"that image file is damaged or unreadable ({type(exc).__name__})") from None
    return ext
