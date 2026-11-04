#!/usr/bin/env python3
"""Render an SVG to PNG with headless Edge/Chrome, then trim white margins."""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def _dimensions(svg: str) -> tuple[float, float]:
    match = re.search(r'viewBox\s*=\s*["\']([^"\']+)["\']', svg)
    if match:
        parts = re.split(r"[,\s]+", match.group(1).strip())
        if len(parts) == 4:
            try:
                return float(parts[2]), float(parts[3])
            except ValueError:
                pass
    width = re.search(r'<svg[^>]*\swidth\s*=\s*["\']([0-9.]+)', svg)
    height = re.search(r'<svg[^>]*\sheight\s*=\s*["\']([0-9.]+)', svg)
    if width and height:
        return float(width.group(1)), float(height.group(1))
    return 900.0, 600.0


def _find_browser(explicit: str | None) -> str | None:
    if explicit:
        return explicit if Path(explicit).is_file() else None
    for candidate in BROWSERS:
        if Path(candidate).is_file():
            return candidate
    for name in ("msedge", "chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def _render_with_browser(browser: str, svg: str, png: Path, width: int, height: int) -> bool:
    svg_body = re.sub(r"<\?xml[^>]*\?>", "", svg, count=1)
    page = f"""<!doctype html><meta charset="utf-8">
<style>html,body{{margin:0;background:#fff}}svg{{display:block;width:100%;height:auto}}</style>
{svg_body}"""
    with tempfile.TemporaryDirectory(prefix="svgrender-") as tmp:
        tmp_path = Path(tmp)
        (tmp_path / "page.html").write_text(page, encoding="utf-8")
        profile = tmp_path / "profile"
        cmd = [
            browser,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=2",
            f"--window-size={width},{height}",
            f"--user-data-dir={profile}",
            f"--screenshot={png.resolve()}",
            (tmp_path / "page.html").resolve().as_uri(),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        except (OSError, subprocess.TimeoutExpired) as exc:
            print(f"browser failed: {exc}", file=sys.stderr)
            return False
        if result.returncode != 0 or not png.is_file():
            print(f"browser exit {result.returncode}: {result.stderr[-300:]}", file=sys.stderr)
            return False
    return True


def _trim(path: Path) -> None:
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    background = Image.new("RGB", image.size, (255, 255, 255))
    diff = ImageChops.difference(image, background).convert("L")
    mask = diff.point(lambda value: 255 if value > 6 else 0)
    box = mask.getbbox()
    if not box:
        return
    pad = 18
    box = (max(0, box[0] - pad), max(0, box[1] - pad),
           min(image.width, box[2] + pad), min(image.height, box[3] + pad))
    image.crop(box).save(path, optimize=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("svg", type=Path)
    parser.add_argument("png", type=Path)
    parser.add_argument("--width", type=int, default=1200)
    parser.add_argument("--browser", default=None)
    args = parser.parse_args()

    if not args.svg.is_file():
        print(f"missing SVG: {args.svg}", file=sys.stderr)
        return 2
    svg = args.svg.read_text(encoding="utf-8")
    vb_width, vb_height = _dimensions(svg)
    width = max(400, args.width)
    height = max(300, min(2400, round(width * vb_height / max(1.0, vb_width))))
    args.png.parent.mkdir(parents=True, exist_ok=True)

    browser = _find_browser(args.browser)
    ok = bool(browser) and _render_with_browser(browser, svg, args.png, width, height)
    if not ok:
        try:
            import cairosvg
            cairosvg.svg2png(bytestring=svg.encode("utf-8"), write_to=str(args.png),
                             output_width=width, background_color="white")
            ok = True
        except Exception as exc:
            print(f"no renderer available (browser/cairosvg): {exc}", file=sys.stderr)
            return 1

    _trim(args.png)
    with Image.open(args.png) as image:
        if min(image.size) < 200:
            print(f"warning: rendered image is small: {image.size}", file=sys.stderr)
    print(f"wrote {args.png} ({args.png.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
