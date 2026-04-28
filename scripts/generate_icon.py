"""Generate macOS .icns app icon for Skyscanner 多市场比价."""
from __future__ import annotations

import math
import subprocess
import shutil
from pathlib import Path
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ICONSET_DIR = PROJECT_ROOT / "build" / "icon.iconset"
ICNS_PATH = PROJECT_ROOT / "data" / "app_icon.icns"

# macOS iconset sizes: name suffix -> pixel size
SIZES = {
    "icon_16x16.png": 16,
    "icon_16x16@2x.png": 32,
    "icon_32x32.png": 32,
    "icon_32x32@2x.png": 64,
    "icon_128x128.png": 128,
    "icon_128x128@2x.png": 256,
    "icon_256x256.png": 256,
    "icon_256x256@2x.png": 512,
    "icon_512x512.png": 512,
    "icon_512x512@2x.png": 1024,
}

BACKGROUND = (30, 100, 200)  # Sky blue
FOREGROUND = (255, 255, 255)  # White


def _rounded_rect_mask(size: int, radius_ratio: float = 0.225) -> Image.Image:
    """Create an anti-aliased rounded-rectangle mask for macOS icon shape."""
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    r = int(size * radius_ratio)
    # Draw a filled rounded rectangle
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    return mask


def _draw_airplane(draw: ImageDraw.Draw, cx: float, cy: float, scale: float) -> None:
    """Draw a simple airplane silhouette centered at (cx, cy) with given scale."""
    # Airplane path as list of (x, y) tuples relative to center
    # Fuselage and wings
    body = [
        (cx - scale * 0.42, cy + scale * 0.02),   # tail bottom
        (cx - scale * 0.38, cy - scale * 0.04),   # tail top
        (cx - scale * 0.10, cy + scale * 0.15),   # left wing root bottom
        (cx + scale * 0.05, cy + scale * 0.18),   # left wing tip
        (cx + scale * 0.12, cy + scale * 0.10),   # left wing tip inner
        (cx + scale * 0.20, cy + scale * 0.12),   # nose bottom
        (cx + scale * 0.38, cy + scale * 0.02),   # nose tip
        (cx + scale * 0.42, cy + scale * 0.00),   # nose tip point
        (cx + scale * 0.38, cy - scale * 0.02),   # nose tip top
        (cx + scale * 0.20, cy - scale * 0.12),   # nose top
        (cx + scale * 0.12, cy - scale * 0.10),   # right wing tip inner
        (cx + scale * 0.05, cy - scale * 0.18),   # right wing tip
        (cx - scale * 0.10, cy - scale * 0.15),   # right wing root top
        (cx - scale * 0.38, cy + scale * 0.04),   # tail top
        (cx - scale * 0.42, cy + scale * 0.00),   # tail center
    ]
    # Tail fin
    fin = [
        (cx - scale * 0.40, cy + scale * 0.00),
        (cx - scale * 0.36, cy - scale * 0.18),
        (cx - scale * 0.25, cy - scale * 0.04),
        (cx - scale * 0.28, cy + scale * 0.02),
    ]
    draw.polygon(body, fill=FOREGROUND)
    draw.polygon(fin, fill=FOREGROUND)


def _generate_icon_image(size: int) -> Image.Image:
    """Generate a single icon image at the given size."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    mask = _rounded_rect_mask(size)

    # Draw gradient background
    draw = ImageDraw.Draw(img)
    for y in range(size):
        t = y / size
        r = int(BACKGROUND[0] + (20 - BACKGROUND[0]) * t)
        g = int(BACKGROUND[1] + (60 - BACKGROUND[1]) * t)
        b = int(BACKGROUND[2] + (160 - BACKGROUND[2]) * t)
        draw.line([(0, y), (size, y)], fill=(r, g, b))

    # Apply rounded rect mask
    alpha = Image.new("L", (size, size), 0)
    alpha_draw = ImageDraw.Draw(alpha)
    r = int(size * 0.225)
    alpha_draw.rounded_rectangle(
        [0, 0, size - 1, size - 1], radius=r, fill=255
    )
    img.putalpha(alpha)

    # Draw airplane silhouette
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    _draw_airplane(overlay_draw, size / 2, size / 2, size * 0.62)

    # Composite overlay onto background with alpha
    img = Image.alpha_composite(img, overlay)

    return img


def main() -> None:
    ICONSET_DIR.mkdir(parents=True, exist_ok=True)
    ICNS_PATH.parent.mkdir(parents=True, exist_ok=True)

    for filename, pixel_size in SIZES.items():
        img = _generate_icon_image(pixel_size)
        img.save(ICONSET_DIR / filename, "PNG")
        print(f"  {filename} ({pixel_size}x{pixel_size})")

    subprocess.run(
        ["iconutil", "-c", "icns", "-o", str(ICNS_PATH), str(ICONSET_DIR)],
        check=True,
    )
    print(f"Icon written to: {ICNS_PATH}")

    shutil.rmtree(ICONSET_DIR)
    print("Cleaned up temporary iconset directory.")


if __name__ == "__main__":
    main()
