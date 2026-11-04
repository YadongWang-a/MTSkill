#!/usr/bin/env python3
"""Crop figure blocks and table crops from corrected page images."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FIGURE_LABELS, TABLE_LABELS, read_json, setup_stdio, write_json

MAX_PAGE_SIDE = 1600
MIN_FIGURE_SIDE = 520
MIN_TABLE_SIDE = 900


def _union_boxes(block: dict) -> list[int] | None:
    boxes = []
    for box in [block.get("bbox")] + list(block.get("regions") or []):
        if isinstance(box, (list, tuple)) and len(box) == 4:
            try:
                boxes.append([int(round(float(v))) for v in box])
            except (TypeError, ValueError):
                continue
    if not boxes:
        return None
    return [
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    ]


def _clamp(box: list[int], width: int, height: int) -> list[int]:
    x1 = max(0, min(width - 1, box[0]))
    y1 = max(0, min(height - 1, box[1]))
    x2 = max(x1 + 1, min(width, box[2]))
    y2 = max(y1 + 1, min(height, box[3]))
    return [x1, y1, x2, y2]


def _correct_page(image: Image.Image, ocr: dict, width: int, height: int,
                  keep_original: bool = False) -> Image.Image:
    rotation = ocr.get("rotation") or {}
    angle = float(rotation.get("total_angle") or 0.0)
    if rotation.get("applied") and abs(angle) > 0.01:
        image = image.rotate(angle, expand=True, resample=Image.Resampling.BICUBIC,
                             fillcolor=(255, 255, 255))
    if not keep_original and image.size != (width, height):
        image = image.resize((width, height), Image.Resampling.LANCZOS)
    return image


def _records(ocr_dir: Path) -> list[dict]:
    manifest = ocr_dir / "manifest.json"
    if manifest.is_file():
        data = read_json(manifest)
        ids = [item["image_id"] for item in data.get("images") or [] if item.get("status") == "ok"]
    else:
        ids = [p.stem for p in sorted(ocr_dir.glob("p*.json"))]
    records = []
    for image_id in ids:
        path = ocr_dir / f"{image_id}.json"
        if path.is_file():
            records.append(read_json(path))
    return records


def _crop_block(image: Image.Image, block: dict, width: int, height: int,
                min_side: int, pad_ratio: float,
                scale_x: float = 1.0, scale_y: float = 1.0) -> tuple[Image.Image, list[int]] | None:
    box = _union_boxes(block)
    if not box:
        return None
    ocr_box = _clamp(box, width, height)
    scaled = [round(ocr_box[0] * scale_x), round(ocr_box[1] * scale_y),
              round(ocr_box[2] * scale_x), round(ocr_box[3] * scale_y)]
    x1, y1, x2, y2 = _clamp(scaled, image.width, image.height)
    pad = max(2, min(24, round(min(image.width, image.height) * pad_ratio)))
    x1, y1 = max(0, x1 - pad), max(0, y1 - pad)
    x2, y2 = min(image.width, x2 + pad), min(image.height, y2 + pad)
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    crop = image.crop((x1, y1, x2, y2))
    if min(crop.size) < min_side:
        scale = min(min_side / max(1, min(crop.size)), 6.0)
        crop = crop.resize(
            (max(1, int(crop.width * scale)), max(1, int(crop.height * scale))),
            Image.Resampling.LANCZOS,
        )
    return crop, [x1, y1, x2, y2]


def _table_format(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return "empty"
    lower = text.lower()
    if "<table" in lower or "<tr" in lower:
        return "html"
    if "otsl" in lower:
        return "otsl"
    return "text"


def build(ocr_dir: Path, out_dir: Path, page_dir: Path,
          table_dir: Path) -> tuple[dict, dict, list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    table_dir.mkdir(parents=True, exist_ok=True)
    page_dir.mkdir(parents=True, exist_ok=True)
    figures: dict[str, dict] = {}
    tables: dict[str, dict] = {}
    pages: dict[str, dict] = {}
    problems: list[str] = []

    for record in _records(ocr_dir):
        image_id = record.get("image_id", "")
        source = Path(record.get("source", ""))
        if not source.is_absolute():
            source = Path.cwd() / source
        if not source.is_file():
            problems.append(f"{image_id}: source image missing: {source}")
            continue

        response = record.get("response") or {}
        ocr = response.get("ocr") or {}
        width = int(ocr.get("image_width") or 0)
        height = int(ocr.get("image_height") or 0)
        if width <= 0 or height <= 0:
            problems.append(f"{image_id}: missing image_width/image_height")
            continue

        try:
            with Image.open(source) as opened:
                image = ImageOps.exif_transpose(opened).convert("RGB")
        except Exception as exc:
            problems.append(f"{image_id}: cannot open source ({exc})")
            continue

        preprocess = record.get("preprocess") or {}
        keep_original = bool(preprocess.get("resized"))
        image = _correct_page(image, ocr, width, height, keep_original=keep_original)
        scale_x = image.width / max(1, width)
        scale_y = image.height / max(1, height)
        page_index = int(record.get("page_index", 0))

        page_file = page_dir / f"{image_id}.jpg"
        thumb = image.copy()
        thumb.thumbnail((MAX_PAGE_SIDE, MAX_PAGE_SIDE), Image.Resampling.LANCZOS)
        thumb.save(page_file, quality=88, optimize=True)
        pages[image_id] = {
            "image_id": image_id,
            "page": page_index,
            "source": record.get("source", ""),
            "page_image": f"{page_dir.name}/{page_file.name}",
            "ocr_width": width,
            "ocr_height": height,
            "display_width": thumb.width,
            "display_height": thumb.height,
        }

        for block in ocr.get("blocks") or []:
            label = block.get("label", "")
            is_figure = label in FIGURE_LABELS
            is_table = label in TABLE_LABELS
            if not (is_figure or is_table):
                continue

            order = int(block.get("order", 0))
            cropped = _crop_block(
                image, block, width, height,
                MIN_FIGURE_SIDE if is_figure else MIN_TABLE_SIDE,
                0.012 if is_figure else 0.006,
                scale_x, scale_y,
            )
            if cropped is None:
                problems.append(f"{image_id}: {label} block {order} has no usable bbox")
                continue
            crop, box = cropped

            if is_figure:
                figure_id = f"{image_id}_b{order:03d}"
                crop_file = out_dir / f"{figure_id}.png"
                crop.save(crop_file, compress_level=6)
                figures[figure_id] = {
                    "id": figure_id,
                    "image_id": image_id,
                    "page": page_index,
                    "order": order,
                    "label": label,
                    "kind_hint": label,
                    "bbox": box,
                    "ocr_width": width,
                    "ocr_height": height,
                    "crop": f"{out_dir.name}/{crop_file.name}",
                    "width": crop.width,
                    "height": crop.height,
                }
            else:
                table_id = f"{image_id}_t{order:03d}"
                crop_file = table_dir / f"{table_id}.png"
                crop.save(crop_file, compress_level=6)
                content = block.get("content") or ""
                tables[table_id] = {
                    "id": table_id,
                    "image_id": image_id,
                    "page": page_index,
                    "block_id": order,
                    "bbox": box,
                    "ocr_width": width,
                    "ocr_height": height,
                    "path": f"{table_dir.name}/{crop_file.name}",
                    "width": crop.width,
                    "height": crop.height,
                    "ocr_format": _table_format(content),
                    "ocr_content": content,
                }

    figure_index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "figures": figures,
        "pages": pages,
    }
    table_index = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tables": tables,
    }
    keep_figures = {Path(item["crop"]).name for item in figures.values()}
    for old in out_dir.glob("p*_b*.png"):
        if old.name not in keep_figures and not old.name.endswith("_redraw.png"):
            old.unlink()
    keep_tables = {Path(item["path"]).name for item in tables.values()}
    for old in table_dir.glob("p*_t*.png"):
        if old.name not in keep_tables:
            old.unlink()
    write_json(out_dir / "index.json", figure_index)
    write_json(table_dir / "index.json", table_index)
    return figure_index, table_index, problems


def check(out_dir: Path, table_dir: Path) -> int:
    problems = []
    figure_index = out_dir / "index.json"
    table_index = table_dir / "index.json"
    if not figure_index.is_file():
        problems.append(f"missing {figure_index}")
        figures = {}
        pages = {}
    else:
        index = read_json(figure_index)
        figures = index.get("figures") or {}
        pages = index.get("pages") or {}
        for asset_id, item in figures.items():
            path = out_dir.parent / item.get("crop", "")
            if not path.is_file():
                problems.append(f"{asset_id}: missing crop {path}")
                continue
            try:
                with Image.open(path) as image:
                    if image.width < 4 or image.height < 4:
                        problems.append(f"{asset_id}: crop too small")
            except Exception as exc:
                problems.append(f"{asset_id}: unreadable crop ({exc})")
        for image_id, item in pages.items():
            path = out_dir.parent / item.get("page_image", "")
            if not path.is_file():
                problems.append(f"{image_id}: missing page image {path}")

    if not table_index.is_file():
        problems.append(f"missing {table_index}")
        tables = {}
    else:
        tables = (read_json(table_index).get("tables") or {})
        for table_id, item in tables.items():
            path = table_dir.parent / item.get("path", "")
            if not path.is_file():
                problems.append(f"{table_id}: missing table crop {path}")
                continue
            try:
                with Image.open(path) as image:
                    if image.width < 4 or image.height < 4:
                        problems.append(f"{table_id}: table crop too small")
            except Exception as exc:
                problems.append(f"{table_id}: unreadable table crop ({exc})")

    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"ok: {len(figures)} figures, {len(tables)} table crops, {len(pages)} pages")
    return 0


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ocr", default="out/ocr")
    parser.add_argument("--out", default="out/figures")
    parser.add_argument("--table-out", default="out/tables")
    parser.add_argument("--page-out", default="out/pageimg")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    ocr_dir = Path(args.ocr)
    out_dir = Path(args.out)
    table_dir = Path(args.table_out)
    page_dir = Path(args.page_out)

    if args.check:
        return check(out_dir, table_dir)
    if not ocr_dir.is_dir():
        print(f"missing OCR directory: {ocr_dir}", file=sys.stderr)
        return 2

    figure_index, table_index, problems = build(ocr_dir, out_dir, page_dir, table_dir)
    print(f"figures: {len(figure_index['figures'])}, tables: {len(table_index['tables'])}, "
          f"pages: {len(figure_index['pages'])}")
    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
