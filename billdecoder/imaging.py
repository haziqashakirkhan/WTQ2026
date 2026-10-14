"""Image loading: fix orientation, normalise colour mode, cap resolution."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageOps


MIN_SIDE = 1600


def load_image_bytes(path: str | Path, max_side: int = 2200) -> bytes:
    """Return JPEG bytes of the bill, EXIF-rotated and downscaled if very large."""
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            background = Image.new("RGB", rgba.size, (255, 255, 255))
            background.paste(rgba, mask=rgba.split()[-1])
            img = background
        else:
            img = img.convert("RGB")
        longest = max(img.size)
        if longest > max_side:
            scale = max_side / longest
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        elif longest < MIN_SIDE:  # small phone photos/screenshots: upscale so tiny text stays legible
            scale = MIN_SIDE / longest
            img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=92, optimize=True)
        return buf.getvalue()
