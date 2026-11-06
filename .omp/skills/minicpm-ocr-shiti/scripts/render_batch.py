#!/usr/bin/env python3
"""Render redraw SVGs from a plan, in batches, with a hard timeout."""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, setup_stdio, write_json


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", default="out/redraw/plan.json")
    parser.add_argument("--batch", default="")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    plan = read_json(plan_path)
    project_root = plan_path.parent.parent
    ids = []
    for batch in plan.get("batches") or []:
        if not args.batch or batch["batch_id"] == args.batch:
            ids.extend(batch.get("figures") or [])
    if not ids:
        ids = list((plan.get("jobs") or {}).keys())
    jobs = [plan["jobs"][fid] for fid in ids if fid in plan.get("jobs", {})]
    renderer = Path(__file__).with_name("render_svg.py")

    def render(job):
        svg = project_root / job["svg"]
        png = project_root / job["png"]
        if not svg.is_file():
            return job["figure_id"], "missing_svg", ""
        if png.is_file() and not args.force:
            return job["figure_id"], "cached", ""
        png.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [sys.executable, str(renderer), str(svg), str(png)],
                capture_output=True, text=True, timeout=args.timeout,
            )
        except subprocess.TimeoutExpired:
            return job["figure_id"], "timeout", ""
        if result.returncode != 0 or not png.is_file():
            return job["figure_id"], "failed", (result.stderr or result.stdout).strip()[-300:]
        return job["figure_id"], "ok", ""

    stats = {}
    with ThreadPoolExecutor(max_workers=max(1, min(6, args.workers))) as pool:
        for fid, status, detail in pool.map(render, jobs):
            stats[fid] = {"status": status, "detail": detail}
            print(f"  {fid}: {status}" + (f" ({detail})" if detail else ""))
    write_json(plan_path.parent / "render.json", stats)
    failures = sum(1 for value in stats.values() if value["status"] in ("failed", "timeout", "missing_svg"))
    print(f"rendered: {len(stats) - failures}/{len(stats)}, failures: {failures}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
