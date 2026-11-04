from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tif", ".tiff"}
FIGURE_LABELS = {"image", "chart"}
TABLE_LABELS = {"table"}
EXCLUDED_DIRS = {"out", ".git", ".venv", ".minicpm-ocr", "__pycache__"}


def setup_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def load_env(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def read_json(path: str | Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: str | Path, obj) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(p)


def natural_key(value: str):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", value)]


def collect_images(inputs: list[str]) -> list[Path]:
    cwd = Path.cwd()
    found: list[Path] = []
    for raw in inputs:
        p = Path(raw)
        if not p.exists():
            raise SystemExit(f"input not found: {raw}")
        if p.is_dir():
            for child in p.rglob("*"):
                if not child.is_file() or child.suffix.lower() not in IMAGE_SUFFIXES:
                    continue
                if any(part in EXCLUDED_DIRS for part in child.parts):
                    continue
                found.append(child.resolve())
        elif p.suffix.lower() in IMAGE_SUFFIXES:
            found.append(p.resolve())
    unique = sorted(set(found), key=lambda x: natural_key(str(x)))
    return unique


def relpath(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()
