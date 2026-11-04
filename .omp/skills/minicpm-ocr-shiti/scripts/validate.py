#!/usr/bin/env python3
"""Validate page extraction or the final question bank."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import FIGURE_LABELS, TABLE_LABELS, read_json, setup_stdio

TYPES = {"choice", "fill", "solve", "judge", "other"}
KINDS = {"function", "geometry", "chart", "photo", "other"}
PREFERRED = {"crop", "redrawn"}
TABLE_FORMATS = {"markdown", "html"}
TABLE_SOURCES = {"ocr", "llm"}
SKIP_TEXT_LABELS = {"header", "footer", "number", "footnote", "aside_text",
                    "header_image", "footer_image"}
FIGURE_ID_RE = re.compile(r"^(p[0-9]+)_b([0-9]+)$")
TABLE_ID_RE = re.compile(r"^(p[0-9]+)_t([0-9]+)$")
QUESTION_ID_RE = re.compile(r"^q[0-9]{3,}$")
BANNED_KEYS = {"student_answer", "student_work", "handwriting"}
PLACEHOLDERS = ("本页仅余", "接上一页", "题干接上", "内容截断", "（续）", "(续)")


def walk_keys(obj, where="root"):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in BANNED_KEYS:
                yield f"{where}.{key}: forbidden field"
            yield from walk_keys(value, f"{where}.{key}")
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            yield from walk_keys(value, f"{where}[{index}]")


def text_problem(text: str) -> str | None:
    if not text:
        return None
    if text.count(r"\(") != text.count(r"\)"):
        return "unbalanced \\(...\\)"
    if text.count(r"\[") != text.count(r"\]"):
        return "unbalanced \\[...\\]"
    if text.count("$") % 2:
        return "unbalanced $...$"
    opens = text.count("{") - text.count(r"\{")
    closes = text.count("}") - text.count(r"\}")
    if opens != closes:
        return "unbalanced braces"
    return None


def sub_text(sub) -> str:
    if isinstance(sub, dict):
        return str(sub.get("text") or sub.get("label") or "").strip()
    return str(sub).strip()


def sub_blocks(sub) -> list[int]:
    if not isinstance(sub, dict):
        return []
    out = []
    for value in sub.get("block_ids") or []:
        if isinstance(value, int):
            out.append(value)
    return out


def bbox_problem(box, width: int | None, height: int | None) -> str | None:
    if box is None:
        return None
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, int) for v in box)):
        return "bbox must be null or four integers"
    x1, y1, x2, y2 = box
    if not (0 <= x1 < x2 and 0 <= y1 < y2):
        return f"invalid bbox {box}"
    if width and height and (x2 > width or y2 > height):
        return f"bbox {box} outside page {width}x{height}"
    return None


def load_ocr(ocr_dir: Path):
    pages = {}
    manifest = ocr_dir / "manifest.json"
    ids = []
    if manifest.is_file():
        ids = [item["image_id"] for item in read_json(manifest).get("images") or []]
    if not ids:
        ids = [p.stem for p in sorted(ocr_dir.glob("p*.json"))]
    for image_id in ids:
        path = ocr_dir / f"{image_id}.json"
        if not path.is_file():
            continue
        record = read_json(path)
        response = record.get("response") or {}
        ocr = response.get("ocr") or {}
        blocks = {}
        for block in ocr.get("blocks") or []:
            try:
                blocks[int(block.get("order"))] = block
            except (TypeError, ValueError):
                continue
        pages[image_id] = {
            "page": int(record.get("page_index", 0)),
            "width": int(ocr.get("image_width") or 0),
            "height": int(ocr.get("image_height") or 0),
            "blocks": blocks,
            "source": record.get("source", ""),
        }
    return pages


def validate_pages(pages_dir: Path, ocr_dir: Path) -> list[str]:
    problems: list[str] = []
    ocr = load_ocr(ocr_dir)
    files = sorted(pages_dir.glob("*.json"))
    if not files:
        return [f"{pages_dir}: no page JSON files"]

    seen_pages = set()
    for path in files:
        try:
            data = read_json(path)
        except Exception as exc:
            problems.append(f"{path.name}: unreadable JSON ({exc})")
            continue
        image_id = data.get("image_id") or path.stem
        where = path.name
        if image_id not in ocr:
            problems.append(f"{where}: image_id {image_id} has no OCR record")
            continue
        seen_pages.add(image_id)
        info = ocr[image_id]
        questions = data.get("questions")
        if not isinstance(questions, list):
            problems.append(f"{where}: questions must be a list")
            continue

        assigned: set[int] = set()
        local_ids = set()
        for index, question in enumerate(questions):
            q = f"{where}.questions[{index}]"
            local_id = question.get("local_id")
            if not local_id:
                problems.append(f"{q}: missing local_id")
            elif local_id in local_ids:
                problems.append(f"{q}: duplicate local_id {local_id}")
            else:
                local_ids.add(local_id)

            stem = (question.get("stem") or "").strip()
            if not stem and not question.get("continued_from_prev"):
                problems.append(f"{q}: empty stem")
            elif stem:
                problem = text_problem(stem)
                if problem:
                    problems.append(f"{q}: stem {problem}")
            for token in PLACEHOLDERS:
                if token in stem:
                    problems.append(f"{q}: placeholder text in stem: {token}")

            q_type = question.get("type")
            if q_type not in TYPES:
                problems.append(f"{q}: bad type {q_type!r}")
            options = question.get("options") or []
            option_keys = []
            for option in options:
                key = (option.get("key") or "").strip()
                text = (option.get("text") or "").strip()
                if not key or not text:
                    problems.append(f"{q}: option with empty key or text")
                else:
                    problem = text_problem(text)
                    if problem:
                        problems.append(f"{q}: option {key} {problem}")
                option_keys.append(key)
            if len(set(option_keys)) != len(option_keys):
                problems.append(f"{q}: duplicate option keys")

            for sub_index, sub in enumerate(question.get("subquestions") or []):
                text = sub_text(sub)
                if not text:
                    problems.append(f"{q}: empty subquestion {sub_index}")
                else:
                    problem = text_problem(text)
                    if problem:
                        problems.append(f"{q}: subquestion {sub_index} {problem}")
                for block_id in sub_blocks(sub):
                    if block_id not in info["blocks"]:
                        problems.append(f"{q}: subquestion block_id {block_id} not in page OCR")
                    else:
                        assigned.add(block_id)

            bbox = question.get("bbox")
            problem = bbox_problem(bbox, info["width"], info["height"])
            if problem:
                problems.append(f"{q}: {problem}")

            for block_id in question.get("block_ids") or []:
                if block_id not in info["blocks"]:
                    problems.append(f"{q}: block_id {block_id} not in page OCR")
                else:
                    assigned.add(int(block_id))

            figure_ids = set()
            for figure in question.get("figures") or []:
                figure_id = figure.get("figure_id") or figure.get("id") or ""
                match = FIGURE_ID_RE.match(figure_id)
                if not match:
                    problems.append(f"{q}: bad figure_id {figure_id!r}")
                    continue
                if match.group(1) != image_id:
                    problems.append(f"{q}: figure {figure_id} belongs to another page")
                    continue
                order = int(match.group(2))
                block = info["blocks"].get(order)
                if block is None:
                    problems.append(f"{q}: figure block {order} not in page OCR")
                    continue
                if block.get("label") not in FIGURE_LABELS:
                    problems.append(f"{q}: block {order} is {block.get('label')}, not a figure")
                if figure_id in figure_ids:
                    problems.append(f"{q}: duplicate figure {figure_id}")
                figure_ids.add(figure_id)
                assigned.add(order)

            table_ids = set()
            for table in question.get("tables") or []:
                table_id = table.get("table_id") or table.get("id") or ""
                match = TABLE_ID_RE.match(table_id)
                if not match:
                    problems.append(f"{q}: bad table_id {table_id!r}")
                    continue
                if match.group(1) != image_id:
                    problems.append(f"{q}: table {table_id} belongs to another page")
                    continue
                order = int(match.group(2))
                if table.get("block_id") not in (None, order):
                    problems.append(f"{q}: table {table_id} block_id mismatch")
                block = info["blocks"].get(order)
                if block is None:
                    problems.append(f"{q}: table block {order} not in page OCR")
                    continue
                if block.get("label") != "table":
                    problems.append(f"{q}: block {order} is {block.get('label')}, not a table")
                if table_id in table_ids:
                    problems.append(f"{q}: duplicate table {table_id}")
                table_ids.add(table_id)
                assigned.add(order)
                if table.get("format") not in TABLE_FORMATS:
                    problems.append(f"{q}/{table_id}: bad table format {table.get('format')!r}")
                if table.get("source") not in TABLE_SOURCES:
                    problems.append(f"{q}/{table_id}: bad table source {table.get('source')!r}")
                if not str(table.get("content") or "").strip():
                    problems.append(f"{q}/{table_id}: empty table content")

        ignored = set()
        for index, item in enumerate(data.get("ignored_blocks") or []):
            order = item.get("order")
            reason = (item.get("reason") or "").strip()
            if order not in info["blocks"]:
                problems.append(f"{where}.ignored_blocks[{index}]: order {order} not in page OCR")
            else:
                ignored.add(int(order))
            if not reason:
                problems.append(f"{where}.ignored_blocks[{index}]: empty reason")

        for order, block in info["blocks"].items():
            label = block.get("label", "")
            if label in FIGURE_LABELS:
                if order not in assigned and order not in ignored:
                    problems.append(f"{where}: figure block {order} ({label}) is neither referenced nor ignored")
                continue
            if label in TABLE_LABELS:
                if order not in assigned and order not in ignored:
                    problems.append(f"{where}: table block {order} ({label}) is neither transcribed nor ignored")
                continue
            if label in SKIP_TEXT_LABELS:
                continue
            if (block.get("content") or "").strip() and order not in assigned and order not in ignored:
                problems.append(f"{where}: text block {order} ({label}) is neither assigned nor ignored")

        for problem in walk_keys(data, where):
            problems.append(problem)

    missing = set(ocr) - seen_pages
    for image_id in sorted(missing):
        problems.append(f"missing page extraction for {image_id}")
    return problems


def validate_questions(path: Path, figures_dir: Path) -> list[str]:
    problems: list[str] = []
    try:
        data = read_json(path)
    except Exception as exc:
        return [f"{path}: unreadable JSON ({exc})"]
    base = path.parent
    index_path = figures_dir / "index.json"
    if not index_path.is_file():
        return [f"missing figure index: {index_path}"]
    index = read_json(index_path)
    figures = index.get("figures") or {}

    questions = data.get("questions")
    if not isinstance(questions, list):
        return ["questions must be a list"]
    meta = data.get("meta") or {}
    if meta.get("question_count") != len(questions):
        problems.append(f"meta.question_count {meta.get('question_count')} != {len(questions)}")
    if not questions:
        problems.append("questions is empty")

    page_map = {info["page"]: info for info in load_ocr_cache.get("pages", {}).values()}
    ids = set()
    for index_no, question in enumerate(questions):
        q = f"questions[{index_no}]"
        qid = question.get("id", "")
        if not QUESTION_ID_RE.match(qid):
            problems.append(f"{q}: bad id {qid!r}")
        elif qid in ids:
            problems.append(f"{q}: duplicate id {qid}")
        else:
            ids.add(qid)

        pages = question.get("pages")
        if not (isinstance(pages, list) and pages and all(isinstance(p, int) and p >= 0 for p in pages)):
            problems.append(f"{q}: pages must be a non-empty list of page indexes")
            pages = []
        for page in pages:
            if page not in page_map:
                problems.append(f"{q}: page {page} has no OCR record")
        if pages:
            content_page = pages[0]
            first_part = (question.get("parts") or [{}])[0]
            if first_part.get("page") in page_map:
                content_page = first_part.get("page")
            if content_page in page_map:
                problem = bbox_problem(question.get("bbox"), page_map[content_page]["width"], page_map[content_page]["height"])
                if problem:
                    problems.append(f"{q}: {problem}")

        for part_index, part in enumerate(question.get("parts") or []):
            page = part.get("page")
            info = page_map.get(page)
            if not info:
                problems.append(f"{q}.parts[{part_index}]: unknown page {page}")
                continue
            problem = bbox_problem(part.get("bbox"), info["width"], info["height"])
            if problem:
                problems.append(f"{q}.parts[{part_index}]: {problem}")

        stem = (question.get("stem") or "").strip()
        gaps = question.get("content_gaps") or []
        if not stem and not gaps:
            problems.append(f"{q}: empty stem and no content_gaps")
        elif stem:
            problem = text_problem(stem)
            if problem:
                problems.append(f"{q}: stem {problem}")
        for token in PLACEHOLDERS:
            if token in stem:
                problems.append(f"{q}: placeholder text in stem: {token}")

        q_type = question.get("type")
        if q_type not in TYPES:
            problems.append(f"{q}: bad type {q_type!r}")
        options = question.get("options") or []
        keys = []
        for option in options:
            key = (option.get("key") or "").strip()
            text = (option.get("text") or "").strip()
            if not key or not text:
                problems.append(f"{q}: option with empty key or text")
            else:
                problem = text_problem(text)
                if problem:
                    problems.append(f"{q}: option {key} {problem}")
            keys.append(key)
        if len(set(keys)) != len(keys):
            problems.append(f"{q}: duplicate option keys")
        for sub_index, sub in enumerate(question.get("subquestions") or []):
            if isinstance(sub, dict):
                problems.append(f"{q}: subquestion {sub_index} must be a string in the final JSON")
                continue
            text = str(sub).strip()
            if not text:
                problems.append(f"{q}: empty subquestion {sub_index}")
            else:
                problem = text_problem(text)
                if problem:
                    problems.append(f"{q}: subquestion {sub_index} {problem}")

        for field in ("subject", "chapter"):
            if not (question.get(field) or "").strip():
                problems.append(f"{q}: empty {field}")
        if not question.get("knowledge_points"):
            problems.append(f"{q}: empty knowledge_points")

        complete = question.get("complete")
        if not isinstance(complete, bool):
            problems.append(f"{q}: complete must be boolean")
        elif complete and gaps:
            problems.append(f"{q}: complete=true but content_gaps is non-empty")
        elif not complete and not gaps:
            problems.append(f"{q}: complete=false but content_gaps is empty")
        if q_type == "choice" and len(options) < 2 and complete is True:
            problems.append(f"{q}: complete choice with fewer than 2 options")

        for figure in question.get("figures") or []:
            figure_id = figure.get("id", "")
            item = figures.get(figure_id)
            if not item:
                problems.append(f"{q}: unknown figure {figure_id}")
                continue
            kind = figure.get("kind")
            if kind not in KINDS:
                problems.append(f"{q}/{figure_id}: bad kind {kind!r}")
            if kind == "table":
                problems.append(f"{q}/{figure_id}: tables must be transcribed as text, not figures")
            preferred = figure.get("preferred")
            if preferred not in PREFERRED:
                problems.append(f"{q}/{figure_id}: bad preferred {preferred!r}")

            crop = figure.get("crop") or item.get("crop")
            if not crop or not (base / crop).is_file():
                problems.append(f"{q}/{figure_id}: missing crop {crop}")

            if preferred == "redrawn":
                for field in ("redraw_svg", "redraw_png"):
                    value = figure.get(field)
                    if not value or not (base / value).is_file():
                        problems.append(f"{q}/{figure_id}: preferred=redrawn but {field} missing")
                if figure.get("self_check") != "passed":
                    problems.append(f"{q}/{figure_id}: preferred=redrawn but self_check != passed")
                if not (figure.get("review_note") or "").strip():
                    problems.append(f"{q}/{figure_id}: preferred=redrawn but review_note empty")

        table_ids = set()
        for table in question.get("tables") or []:
            table_id = table.get("table_id") or table.get("id") or ""
            if not TABLE_ID_RE.match(table_id):
                problems.append(f"{q}: bad table_id {table_id!r}")
            elif table_id in table_ids:
                problems.append(f"{q}: duplicate table {table_id}")
            else:
                table_ids.add(table_id)
            fmt = table.get("format")
            content = str(table.get("content") or "").strip()
            if fmt not in TABLE_FORMATS:
                problems.append(f"{q}/{table_id}: bad table format {fmt!r}")
            elif not content:
                problems.append(f"{q}/{table_id}: empty table content")
            elif fmt == "markdown":
                lines = content.splitlines()
                has_separator = any(re.match(r"^\s*\|?\s*:?-{3,}", line) for line in lines)
                if "|" not in content or not has_separator:
                    problems.append(f"{q}/{table_id}: markdown table needs a header separator")
            elif fmt == "html":
                lower = content.lower()
                if "<table" not in lower or "</table>" not in lower:
                    problems.append(f"{q}/{table_id}: html table is incomplete")
            if table.get("source") not in TABLE_SOURCES:
                problems.append(f"{q}/{table_id}: bad table source {table.get('source')!r}")

        for problem in walk_keys(question, q):
            problems.append(problem)

    return problems


load_ocr_cache: dict = {}


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=Path)
    parser.add_argument("--ocr", type=Path, default=Path("out/ocr"))
    parser.add_argument("--questions", type=Path)
    parser.add_argument("--figures", type=Path, default=Path("out/figures"))
    args = parser.parse_args()

    if bool(args.pages) == bool(args.questions):
        parser.error("choose exactly one of --pages or --questions")

    if args.pages:
        problems = validate_pages(args.pages, args.ocr)
        label = f"pages: {len(list(args.pages.glob('*.json')))}"
    else:
        load_ocr_cache["pages"] = load_ocr(args.ocr)
        problems = validate_questions(args.questions, args.figures)
        try:
            count = len(read_json(args.questions).get("questions") or [])
        except Exception:
            count = 0
        label = f"questions: {count}"

    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print(f"ok: {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
