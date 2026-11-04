#!/usr/bin/env python3
"""Upload photos to MiniCPM-OCR and store one page JSON per image."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests
from PIL import Image, ImageOps
from requests.adapters import HTTPAdapter

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import collect_images, load_env, read_json, relpath, setup_stdio, write_json

DEFAULT_BASE = "https://api.modelbest.ai"
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_SIDE = 2500

_stats_lock = threading.Lock()
_stats = {"retries": 0, "rate_limited": 0, "connection_errors": 0}
_thread_local = threading.local()


class ApiFailure(RuntimeError):
    def __init__(self, status: int, message: str, retry_after: float | None = None,
                 kind: str = "http"):
        super().__init__(f"HTTP {status}: {message}" if status else message)
        self.status = status
        self.retry_after = retry_after
        self.kind = kind
        self.retryable = kind in ("network", "timeout") or status in (429, 500, 502, 503, 504)


def _bump(key: str) -> None:
    with _stats_lock:
        _stats[key] += 1


def _error_message(raw: str) -> str:
    try:
        payload = json.loads(raw)
        err = payload.get("error") or {}
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or raw)
        return str(err or raw)
    except json.JSONDecodeError:
        return raw.strip()[:500]


def _session(workers: int) -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = HTTPAdapter(pool_connections=max(2, workers * 2),
                              pool_maxsize=max(2, workers * 2), max_retries=0)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"Accept": "application/json", "Connection": "keep-alive"})
        _thread_local.session = session
    return session


def _request(method: str, url: str, key: str, workers: int, payload=None,
             files=None, timeout: float = 180.0):
    headers = {"Authorization": f"Bearer {key}"}
    session = _session(workers)
    try:
        response = session.request(method, url, headers=headers, json=payload, files=files,
                                   timeout=(10, timeout))
    except requests.exceptions.Timeout:
        raise ApiFailure(0, "network timeout", kind="timeout") from None
    except (requests.exceptions.ConnectionError, requests.exceptions.ChunkedEncodingError) as exc:
        raise ApiFailure(0, f"connection reset: {exc}", kind="network") from None

    if response.status_code >= 400:
        retry_after = response.headers.get("Retry-After")
        try:
            delay = float(retry_after) if retry_after else None
        except ValueError:
            delay = None
        raise ApiFailure(response.status_code, _error_message(response.text), delay, kind="http")
    raw = response.text
    return json.loads(raw) if raw.strip() else {}


def _with_retry(label: str, fn, attempts: int = 5):
    delay = 1.5
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ApiFailure as exc:
            if not exc.retryable or attempt == attempts:
                raise
            _bump("retries")
            if exc.status == 429:
                _bump("rate_limited")
            if exc.kind in ("network", "timeout"):
                _bump("connection_errors")
            wait = exc.retry_after if exc.retry_after is not None else delay * random.uniform(0.75, 1.25)
            wait = min(30.0, wait)
            print(f"  {label}: {exc}; retry {attempt}/{attempts - 1} in {wait:.1f}s", flush=True)
            time.sleep(wait)
            delay = min(delay * 2, 30)
    raise RuntimeError("unreachable")


def prepare_upload(path: Path, max_side: int, tmp_dir: Path, image_id: str) -> tuple[Path, dict]:
    with Image.open(path) as opened:
        image = ImageOps.exif_transpose(opened).convert("RGB")
    width, height = image.size
    if max_side <= 0 or max(width, height) <= max_side:
        return path, {
            "resized": False,
            "original_width": width,
            "original_height": height,
            "max_side": max_side,
        }
    scale = max_side / max(width, height)
    target = (max(1, round(width * scale)), max(1, round(height * scale)))
    image = image.resize(target, Image.Resampling.LANCZOS)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"{image_id}.jpg"
    image.save(tmp_path, quality=92, optimize=True)
    return tmp_path, {
        "resized": True,
        "original_width": width,
        "original_height": height,
        "upload_width": target[0],
        "upload_height": target[1],
        "max_side": max_side,
    }


def upload_file(path: Path, base: str, key: str, workers: int, timeout: float) -> str:
    size = path.stat().st_size
    if size > MAX_UPLOAD_BYTES:
        raise RuntimeError(f"{path}: {size} bytes exceeds the 64 MiB upload limit")
    if size > MAX_IMAGE_BYTES:
        print(f"  warning: {path.name} is {size / 1048576:.1f} MiB; the sync image limit is 24 MiB", flush=True)
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    with path.open("rb") as handle:
        files = {"file": (f"upload{path.suffix.lower() or '.bin'}", handle, mime)}
        payload = _request("POST", f"{base}/v1/files", key, workers, files=files, timeout=timeout)
    file_id = payload.get("file_id")
    if not file_id:
        raise RuntimeError(f"upload response has no file_id: {payload}")
    return str(file_id)


def parse_file(file_id: str, base: str, key: str, workers: int, model: str, timeout: float) -> dict:
    payload = {
        "model": model,
        "file_id": file_id,
        "upright": True,
        "recognize_seal": True,
    }
    return _request("POST", f"{base}/v1/document-parse", key, workers,
                    payload=payload, timeout=timeout)


def valid_result(path: Path) -> bool:
    try:
        data = read_json(path)
    except Exception:
        return False
    blocks = ((data.get("response") or {}).get("ocr") or {}).get("blocks")
    return isinstance(blocks, list) and len(blocks) > 0


def entry_from_file(path: Path) -> dict:
    data = read_json(path)
    response = data.get("response") or {}
    ocr = response.get("ocr") or {}
    preprocess = data.get("preprocess") or {}
    return {
        "image_id": data.get("image_id", path.stem),
        "source": data.get("source", ""),
        "page_index": int(data.get("page_index", 0)),
        "file_id": data.get("file_id"),
        "blocks": len(ocr.get("blocks") or []),
        "resized": bool(preprocess.get("resized")),
        "upload_width": preprocess.get("upload_width"),
        "upload_height": preprocess.get("upload_height"),
        "status": "ok",
        "error": None,
    }


def process_one(index: int, image: Path, out: Path, base: str, key: str, model: str,
                timeout: float, force: bool, max_side: int, workers: int) -> dict:
    image_id = f"p{index:03d}"
    target = out / f"{image_id}.json"
    if target.exists() and valid_result(target) and not force:
        return entry_from_file(target)

    upload_path, preprocess = prepare_upload(image, max_side, out / ".uploads", image_id)
    try:
        file_id = _with_retry(
            f"{image_id} upload",
            lambda: upload_file(upload_path, base, key, workers, timeout),
        )
        response = _with_retry(
            f"{image_id} parse",
            lambda: parse_file(file_id, base, key, workers, model, timeout),
        )
    finally:
        if preprocess.get("resized"):
            upload_path.unlink(missing_ok=True)

    blocks = ((response.get("ocr") or {}).get("blocks") or [])
    if not blocks:
        raise RuntimeError("OCR returned no blocks")

    record = {
        "image_id": image_id,
        "source": relpath(image),
        "page_index": index,
        "model": model,
        "file_id": file_id,
        "preprocess": preprocess,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "response": response,
    }
    write_json(target, record)
    return {
        "image_id": image_id,
        "source": record["source"],
        "page_index": index,
        "file_id": file_id,
        "blocks": len(blocks),
        "resized": bool(preprocess.get("resized")),
        "upload_width": preprocess.get("upload_width"),
        "upload_height": preprocess.get("upload_height"),
        "status": "ok",
        "error": None,
    }


def check(out: Path) -> int:
    manifest_path = out / "manifest.json"
    if not manifest_path.is_file():
        print(f"missing {manifest_path}", file=sys.stderr)
        return 1
    manifest = read_json(manifest_path)
    problems = []
    images = manifest.get("images") or []
    if not images:
        problems.append("manifest has no images")
    seen_pages = {}
    for item in images:
        image_id = item.get("image_id", "?")
        page_index = item.get("page_index")
        if page_index in seen_pages:
            problems.append(f"{image_id}: duplicate page_index {page_index} (also {seen_pages[page_index]})")
        else:
            seen_pages[page_index] = image_id
        target = out / f"{image_id}.json"
        if not target.is_file():
            problems.append(f"{image_id}: missing {target.name}")
            continue
        try:
            data = read_json(target)
        except Exception as exc:
            problems.append(f"{image_id}: unreadable JSON ({exc})")
            continue
        ocr = (data.get("response") or {}).get("ocr") or {}
        blocks = ocr.get("blocks") or []
        if int(data.get("page_index", -1)) != page_index:
            problems.append(f"{image_id}: JSON page_index {data.get('page_index')} != manifest {page_index}")
        if not blocks:
            problems.append(f"{image_id}: no blocks")
        if not ocr.get("image_width") or not ocr.get("image_height"):
            problems.append(f"{image_id}: missing image_width/image_height")
    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"ok: {len(images)} pages, {sum(int(i.get('blocks') or 0) for i in images)} blocks")
    return 0


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", nargs="+", default=["pic"], help="image files or directories")
    parser.add_argument("--out", default="out/ocr")
    parser.add_argument("--model", default="MiniCPM-OCR-1.0")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--env", default=".env")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE,
                        help="downscale longest side before upload; 0 disables")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    if args.check:
        return check(out)

    load_env(args.env)
    key = os.environ.get("MODELBEST_API_KEY", "").strip()
    base = (args.base_url or os.environ.get("MODELBEST_BASE_URL") or DEFAULT_BASE).rstrip("/")
    images = collect_images(args.input)
    if not images:
        print("no images found", file=sys.stderr)
        return 2

    if args.dry_run:
        print(f"base={base} model={args.model} key={'set' if key else 'MISSING'} "
              f"max_side={args.max_side} workers={args.workers}")
        for index, image in enumerate(images):
            with Image.open(image) as opened:
                width, height = opened.size
            side = max(width, height)
            action = "resize" if args.max_side > 0 and side > args.max_side else "keep"
            print(f"  p{index:03d} <- {relpath(image)} ({width}x{height}, {action})")
        return 0 if key else 2

    if not key:
        print("MODELBEST_API_KEY is missing; put it in .env or the environment", file=sys.stderr)
        return 2

    out.mkdir(parents=True, exist_ok=True)
    results: list[dict | None] = [None] * len(images)
    failures = 0
    workers = max(1, min(args.workers, 8))
    print(f"OCR {len(images)} image(s) with {args.model} at {base} "
          f"({workers} workers, max_side={args.max_side})", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(process_one, index, image, out, base, key, args.model,
                        args.timeout, args.force, args.max_side, workers): index
            for index, image in enumerate(images)
        }
        for future in as_completed(futures):
            index = futures[future]
            image_id = f"p{index:03d}"
            try:
                entry = future.result()
                results[index] = entry
                size = ""
                if entry.get("resized"):
                    size = f", {entry.get('upload_width')}x{entry.get('upload_height')}"
                print(f"  {image_id}: {entry['blocks']} blocks{size}", flush=True)
            except Exception as exc:
                failures += 1
                results[index] = {
                    "image_id": image_id,
                    "source": relpath(images[index]),
                    "page_index": index,
                    "file_id": None,
                    "blocks": 0,
                    "resized": False,
                    "status": "failed",
                    "error": str(exc),
                }
                print(f"  {image_id}: FAILED {exc}", file=sys.stderr, flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "base_url": base,
        "model": args.model,
        "max_side": args.max_side,
        "images": results,
    }
    write_json(out / "manifest.json", manifest)
    print(f"manifest: {out / 'manifest.json'} ({len(images) - failures}/{len(images)} ok)", flush=True)
    print(f"requests: retries={_stats['retries']} rate_limited={_stats['rate_limited']} "
          f"connection_errors={_stats['connection_errors']}", flush=True)
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
