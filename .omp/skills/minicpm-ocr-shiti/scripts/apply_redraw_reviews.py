#!/usr/bin/env python3
"""Apply batch review verdicts to the question bank."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, setup_stdio, write_json


def load_reviews(directory: Path) -> dict:
    results = {}
    if not directory.is_dir():
        return results
    for path in directory.glob("*.json"):
        try:
            data = read_json(path)
        except Exception:
            continue
        for item in data.get("results") or []:
            fid = item.get("figure_id")
            if fid:
                results[fid] = item
    return results


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default="out/questions.json")
    parser.add_argument("--plan", default="out/redraw/plan.json")
    parser.add_argument("--reviews", default="out/redraw/reviews")
    parser.add_argument("--parts", default="out/question_parts")
    args = parser.parse_args()

    plan = read_json(args.plan)
    jobs = plan.get("jobs") or {}
    reviews = load_reviews(Path(args.reviews))
    questions_path = Path(args.questions)

    def apply(figure: dict) -> None:
        fid = figure.get("id")
        kind = figure.get("kind")
        job = jobs.get(fid)
        if kind == "apparatus":
            figure.update(preferred="crop", self_check=None,
                          redraw_svg=None, redraw_png=None,
                          review_note="化学装置图，按规则不重绘")
            return
        if not job:
            figure.update(preferred="crop", self_check=None,
                          redraw_svg=None, redraw_png=None,
                          review_note="按重绘范围保留切图")
            return
        result = reviews.get(fid) or {}
        passed = bool(result.get("pass"))
        reason = str(result.get("reason") or "").strip()
        figure["redraw_svg"] = job["svg"]
        figure["redraw_png"] = job["png"]
        if passed:
            figure.update(preferred="redrawn", self_check="passed",
                          review_note=reason or "独立复核通过")
        else:
            figure.update(preferred="crop", self_check="failed",
                          review_note=reason or "未复核/超时，回退原图")

    def process(path: Path) -> None:
        data = read_json(path)
        for question in data.get("questions") or []:
            for figure in question.get("figures") or []:
                apply(figure)
        if "meta" in data:
            qs = data["questions"]
            figures = {f["id"] for q in qs for f in q.get("figures") or []}
            redrawn = {f["id"] for q in qs for f in q.get("figures") or []
                       if f.get("preferred") == "redrawn"}
            tables = {t["table_id"] for q in qs for t in q.get("tables") or []}
            data["meta"].update(question_count=len(qs), figure_count=len(figures),
                                table_count=len(tables), redrawn_count=len(redrawn))
        write_json(path, data)

    process(questions_path)
    parts = Path(args.parts)
    if parts.is_dir():
        for path in parts.glob("*.json"):
            process(path)
    print(f"applied reviews: {len(reviews)}; questions: {questions_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
