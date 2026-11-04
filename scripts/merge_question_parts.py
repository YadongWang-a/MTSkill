#!/usr/bin/env python3
"""Combine subject/page part JSONs into the final question bank."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import natural_key, read_json, setup_stdio, write_json


def flatten_sub(sub):
    if not isinstance(sub, dict):
        return str(sub).strip()
    label = str(sub.get("label") or "").strip()
    text = str(sub.get("text") or "").strip()
    if label and not text.startswith(label):
        return f"{label} {text}".strip()
    return text


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parts", default="out/question_parts")
    parser.add_argument("--out", default="out/questions.json")
    parser.add_argument("--source-dir", default="pic")
    parser.add_argument("--model", default="MiniCPM-OCR-1.0")
    args = parser.parse_args()

    parts_dir = Path(args.parts)
    files = sorted(parts_dir.glob("*.json"), key=lambda p: natural_key(p.name))
    if not files:
        print(f"no part JSON under {parts_dir}", file=sys.stderr)
        return 2

    questions = []
    for path in files:
        data = read_json(path)
        items = data.get("questions") if isinstance(data, dict) else None
        if not isinstance(items, list):
            print(f"{path}: expected {{\"questions\": [...]}}", file=sys.stderr)
            return 2
        for item in items:
            item = dict(item)
            item["subquestions"] = [flatten_sub(sub) for sub in item.get("subquestions") or []]
            questions.append(item)
        print(f"  {path.name}: {len(items)}")

    for index, question in enumerate(questions, start=1):
        question["id"] = f"q{index:03d}"

    figures = {
        figure.get("id")
        for question in questions
        for figure in question.get("figures") or []
        if figure.get("id")
    }
    redrawn = {
        figure.get("id")
        for question in questions
        for figure in question.get("figures") or []
        if figure.get("preferred") == "redrawn"
    }
    table_ids = {
        table.get("table_id")
        for question in questions
        for table in question.get("tables") or []
        if table.get("table_id")
    }
    document = {
        "meta": {
            "source_dir": args.source_dir,
            "question_count": len(questions),
            "figure_count": len(figures),
            "table_count": len(table_ids),
            "redrawn_count": len(redrawn),
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "ocr_model": args.model,
        },
        "questions": questions,
    }
    write_json(args.out, document)
    print(f"wrote {args.out}: {len(questions)} questions, {len(figures)} figures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
