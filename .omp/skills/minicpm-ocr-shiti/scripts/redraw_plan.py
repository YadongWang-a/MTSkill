#!/usr/bin/env python3
"""Build a batched redraw plan from the question bank."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, setup_stdio, write_json

DEFAULT_KINDS = ("function", "geometry", "chart")


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default="out/questions.json")
    parser.add_argument("--out-dir", default="out/redraw")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--include-kinds", default=",".join(DEFAULT_KINDS),
                        help="comma-separated kinds; default function,geometry,chart")
    args = parser.parse_args()

    questions = read_json(args.questions).get("questions") or []
    include = {k.strip() for k in args.include_kinds.split(",") if k.strip()}
    out_dir = Path(args.out_dir)
    out_root = out_dir.parent
    jobs_dir = out_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    jobs = []
    skipped = []
    seen = set()
    for question in questions:
        for figure in question.get("figures") or []:
            fid = figure.get("id")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            kind = figure.get("kind", "other")
            if kind == "apparatus":
                skipped.append({"id": fid, "reason": "chemical apparatus"})
                continue
            if kind not in include and not figure.get("redraw_candidate"):
                skipped.append({"id": fid, "reason": f"kind={kind} not in redraw scope"})
                continue
            crop = figure.get("crop")
            if not crop:
                skipped.append({"id": fid, "reason": "missing crop path"})
                continue
            job = {
                "figure_id": fid,
                "question_id": question.get("id"),
                "kind": kind,
                "role": figure.get("role") or "",
                "stem": question.get("stem") or "",
                "crop": crop,
                "crop_abs": str((out_root / crop).resolve()),
                "svg": f"figures/{fid}.svg",
                "png": f"figures/{fid}_redraw.png",
                "svg_abs": str((out_root / "figures" / f"{fid}.svg").resolve()),
                "png_abs": str((out_root / "figures" / f"{fid}_redraw.png").resolve()),
                "budget": {"tool_calls": 3, "timeout_sec": 180},
            }
            jobs.append(job)

    size = max(1, min(8, args.batch_size))
    batches = []
    for i in range(0, len(jobs), size):
        chunk = jobs[i:i + size]
        batch_id = f"batch_{len(batches) + 1:02d}"
        batches.append({"batch_id": batch_id, "figures": [j["figure_id"] for j in chunk]})

    for job in jobs:
        write_json(jobs_dir / f"{job['figure_id']}.json", job)

    plan = {
        "batches": batches,
        "jobs": {j["figure_id"]: j for j in jobs},
        "skipped": skipped,
    }
    write_json(out_dir / "plan.json", plan)
    print(f"candidates: {len(jobs)}, skipped: {len(skipped)}, batches: {len(batches)}")
    for batch in batches:
        print(f"  {batch['batch_id']}: {', '.join(batch['figures'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
