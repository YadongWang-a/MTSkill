#!/usr/bin/env python3
"""Build a contact sheet (crop vs redraw) for one review batch."""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, setup_stdio, write_json

CELL_W = 840
IMG_MAX = (400, 360)
PAD = 14
LABEL_H = 34


def _font(size: int):
    for name in ("arial.ttf", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _fit(path: Path) -> Image.Image | None:
    if not path.is_file():
        return None
    with Image.open(path) as opened:
        image = opened.convert("RGB")
    image.thumbnail(IMG_MAX, Image.Resampling.LANCZOS)
    return image


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="out/redraw/plan.json")
    parser.add_argument("--batch", required=True)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    plan = read_json(plan_path)
    root = plan_path.parent.parent
    batch = next((b for b in plan.get("batches") or [] if b["batch_id"] == args.batch), None)
    if not batch:
        print(f"unknown batch: {args.batch}", file=sys.stderr)
        return 2
    jobs = [plan["jobs"][fid] for fid in batch["figures"] if fid in plan.get("jobs", {})]
    if not jobs:
        print("batch has no jobs", file=sys.stderr)
        return 2

    columns = 2
    rows = math.ceil(len(jobs) / columns)
    cell_h = LABEL_H + IMG_MAX[1] + PAD * 2
    width = PAD + columns * (CELL_W + PAD)
    height = PAD + rows * cell_h + PAD
    sheet = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(sheet)
    title_font = _font(20)
    small_font = _font(14)
    draw.text((PAD, 4), f"{args.batch}  CROP | REDRAW", fill="black", font=title_font)

    for index, job in enumerate(jobs):
        fid = job["figure_id"]
        row, col = divmod(index, columns)
        x0 = PAD + col * (CELL_W + PAD)
        y0 = PAD + row * cell_h + LABEL_H
        crop = _fit(root / job["crop"])
        redraw = _fit(root / job["png"]) if job.get("png") else None
        draw.text((x0, y0 - LABEL_H + 8), f"{fid}  ({job.get('kind', '')})", fill="black", font=title_font)
        for offset, (image, label) in enumerate(((crop, "CROP"), (redraw, "REDRAW"))):
            ix = x0 + offset * (CELL_W // 2 + 6)
            iy = y0
            if image is None:
                draw.rectangle([ix, iy, ix + CELL_W // 2 - 8, iy + IMG_MAX[1]], outline="black")
                draw.text((ix + 12, iy + 12), "MISSING", fill="black", font=small_font)
            else:
                sheet.paste(image, (ix, iy))
                draw.rectangle([ix, iy, ix + image.width, iy + image.height], outline="black")
            draw.text((ix + 4, iy + IMG_MAX[1] + 2), label, fill="black", font=small_font)

    out_path = Path(args.out) if args.out else plan_path.parent / "review" / f"{args.batch}.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, optimize=True)
    write_json(out_path.with_suffix(".json"), {
        "batch": args.batch,
        "sheet": str(out_path),
        "figures": [job["figure_id"] for job in jobs],
    })
    print(f"wrote {out_path} ({sheet.width}x{sheet.height})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
