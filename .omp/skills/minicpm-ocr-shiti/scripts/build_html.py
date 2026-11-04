#!/usr/bin/env python3
"""Render the question bank as a self-contained review page."""

from __future__ import annotations

import argparse
import html
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, setup_stdio

KIND_LABELS = {
    "function": "函数图",
    "geometry": "几何图",
    "chart": "统计图",
    "photo": "照片",
    "other": "配图",
}
TYPE_LABELS = {
    "choice": "选择",
    "fill": "填空",
    "solve": "解答",
    "judge": "判断",
    "other": "其他",
}


def esc(value) -> str:
    return html.escape(str(value or ""), quote=True)


def text_block(value) -> str:
    return esc(value).replace("\n", "<br>")


def render_figure(figure: dict, base: Path) -> tuple[str, list[str]]:
    problems = []
    figure_id = figure.get("id", "")
    crop = figure.get("crop", "")
    redraw_png = figure.get("redraw_png")
    preferred = figure.get("preferred")
    src = crop
    badge = "切图"
    extra = ""
    if preferred == "redrawn":
        if redraw_png and (base / redraw_png).is_file():
            src = redraw_png
            badge = "重绘"
            extra = f'<a class="mini" href="{esc(crop)}" target="_blank" rel="noopener">原切图</a>'
        else:
            badge = "切图·重绘缺失"
    if not src or not (base / src).is_file():
        problems.append(f"{figure_id}: missing image {src}")
        return f'<div class="fig missing">缺图：{esc(figure_id)}</div>', problems

    kind = KIND_LABELS.get(figure.get("kind"), "配图")
    role = figure.get("role") or ""
    note = figure.get("review_note") or ""
    caption = f"{kind}"
    if role:
        caption += f" · {role}"
    if badge == "重绘" and note:
        caption += f" · {note}"
    return (
        f'<figure class="fig">'
        f'<img loading="lazy" src="{esc(src)}" alt="{esc(figure_id)}">'
        f'<figcaption><span class="badge {esc(badge)}">{esc(badge)}</span>'
        f'<span>{esc(caption)}</span>{extra}</figcaption>'
        f'</figure>',
        problems,
    )


def _sanitize_table_html(fragment: str) -> str:
    text = re.sub(r"<script\b.*?</script>", "", fragment, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"\son\w+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)", "", text, flags=re.IGNORECASE)
    return re.sub(r"javascript:", "", text, flags=re.IGNORECASE)


def _markdown_table(markdown: str) -> str:
    lines = [line.strip() for line in markdown.strip().splitlines() if line.strip()]
    if len(lines) < 2:
        return ""
    def cells(line: str) -> list[str]:
        line = line.strip()
        if line.startswith("|"):
            line = line[1:]
        if line.endswith("|"):
            line = line[:-1]
        return [cell.strip() for cell in line.split("|")]
    header = cells(lines[0])
    separator = cells(lines[1])
    if not header or len(separator) != len(header):
        return ""
    if not all(re.fullmatch(r":?-{3,}:?", cell) for cell in separator):
        return ""
    align = []
    for cell in separator:
        if cell.startswith(":") and cell.endswith(":"):
            align.append("center")
        elif cell.endswith(":"):
            align.append("right")
        else:
            align.append("left")
    out = ["<table><thead><tr>"]
    out.extend(f'<th style="text-align:{align[i]}">{esc(cell)}</th>' for i, cell in enumerate(header))
    out.append("</tr></thead><tbody>")
    for line in lines[2:]:
        row = cells(line)
        row += [""] * (len(header) - len(row))
        out.append("<tr>")
        out.extend(f'<td style="text-align:{align[i]}">{esc(cell)}</td>' for i, cell in enumerate(row[:len(header)]))
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render_table(table: dict) -> str:
    table_id = table.get("table_id") or table.get("id") or ""
    content = str(table.get("content") or "").strip()
    fmt = table.get("format")
    if fmt == "html":
        body = _sanitize_table_html(content)
    elif fmt == "markdown":
        body = _markdown_table(content) or f"<pre class='table-fallback'>{esc(content)}</pre>"
    else:
        body = f"<pre class='table-fallback'>{esc(content)}</pre>"
    source = "LLM 转写" if table.get("source") == "llm" else "OCR"
    note = str(table.get("note") or "").strip()
    caption = f"{source} 表格"
    if note:
        caption += f" · {note}"
    return (
        f'<div class="table-wrap" data-table="{esc(table_id)}">'
        f'{body}<div class="table-cap">{esc(caption)}</div></div>'
    )


def render_sources(question: dict, page_map: dict[int, dict], base: Path) -> tuple[str, list[str]]:
    problems = []
    parts = question.get("parts") or []
    if not parts:
        parts = [{"page": (question.get("pages") or [None])[0], "bbox": question.get("bbox")}]
    blocks = []
    for part in parts:
        page = part.get("page")
        if page is None:
            problems.append(f"{question.get('id')}: part without page")
            continue
        info = page_map.get(page)
        if not info:
            problems.append(f"{question.get('id')}: unknown page {page}")
            continue
        page_image = info.get("page_image", "")
        if not page_image or not (base / page_image).is_file():
            problems.append(f"{question.get('id')}: missing page image {page_image}")
            continue
        bbox = part.get("bbox")
        box_html = ""
        if bbox:
            width = max(1, int(info.get("ocr_width") or 1))
            height = max(1, int(info.get("ocr_height") or 1))
            left = bbox[0] / width * 100
            top = bbox[1] / height * 100
            w = (bbox[2] - bbox[0]) / width * 100
            h = (bbox[3] - bbox[1]) / height * 100
            box_html = (f'<span class="box" style="left:{left:.3f}%;top:{top:.3f}%;'
                        f'width:{w:.3f}%;height:{h:.3f}%"></span>')
        blocks.append(
            f'<div class="source"><img loading="lazy" src="{esc(page_image)}" alt="第 {int(page) + 1} 页">'
            f'{box_html}<span class="page-tag">第 {int(page) + 1} 页</span></div>'
        )
    return "".join(blocks), problems


def build(questions_path: Path, figures_dir: Path, out_path: Path) -> tuple[str, list[str]]:
    data = read_json(questions_path)
    index = read_json(figures_dir / "index.json")
    page_map = {info["page"]: info for info in (index.get("pages") or {}).values()}
    questions = data.get("questions") or []
    base = out_path.parent
    problems: list[str] = []

    subjects = sorted({q.get("subject", "") for q in questions if q.get("subject")})
    complete_count = sum(1 for q in questions if q.get("complete"))
    gap_count = len(questions) - complete_count
    figure_ids = {f.get("id") for q in questions for f in (q.get("figures") or [])}
    redrawn_count = len({
        f.get("id") for q in questions for f in (q.get("figures") or [])
        if f.get("preferred") == "redrawn"
    })
    table_ids = {
        t.get("table_id") for q in questions for t in (q.get("tables") or [])
    }

    cards = []
    for question in questions:
        qid = question.get("id", "")
        subject = question.get("subject", "未判定")
        complete = bool(question.get("complete"))
        figures = question.get("figures") or []
        figure_html = []
        for figure in figures:
            rendered, figure_problems = render_figure(figure, base)
            figure_html.append(rendered)
            problems.extend(figure_problems)
        sources_html, source_problems = render_sources(question, page_map, base)
        problems.extend(source_problems)
        tables_html = "".join(render_table(table) for table in question.get("tables") or [])

        options = "".join(
            f'<li><b>{esc(option.get("key"))}.</b> {text_block(option.get("text"))}</li>'
            for option in question.get("options") or []
        )
        subquestions = "".join(
            f'<li>{text_block(sub)}</li>' for sub in question.get("subquestions") or []
        )
        knowledge = "".join(
            f'<span class="chip">{esc(point)}</span>' for point in question.get("knowledge_points") or []
        )
        gaps = "".join(f'<li>{text_block(gap)}</li>' for gap in question.get("content_gaps") or [])
        uncertainties = "".join(
            f'<li>{text_block(item)}</li>' for item in question.get("uncertainties") or []
        )
        search = " ".join([
            qid, str(question.get("stem") or ""), subject, str(question.get("chapter") or ""),
            " ".join(question.get("knowledge_points") or []),
            " ".join(str(o.get("text") or "") for o in question.get("options") or []),
            " ".join(str(t.get("content") or "") for t in question.get("tables") or []),
        ]).lower()

        cards.append(f"""
<article class="card" data-subject="{esc(subject)}" data-complete="{str(complete).lower()}" data-search="{esc(search)}">
  <header>
    <span class="qid">{esc(qid)}</span>
    <span class="badge">{esc(subject)}</span>
    <span class="badge">{esc(question.get('chapter'))}</span>
    <span class="badge">{esc(TYPE_LABELS.get(question.get('type'), '其他'))}</span>
    <span class="badge {'ok' if complete else 'gap'}">{'完整' if complete else '有缺口'}</span>
  </header>
  <div class="stem">{text_block(question.get('stem'))}</div>
  {'<div class="tables">' + tables_html + '</div>' if tables_html else ''}
  {'<ol class="options">' + options + '</ol>' if options else ''}
  {'<ol class="subs">' + subquestions + '</ol>' if subquestions else ''}
  {('<div class="figs">' + ''.join(figure_html) + '</div>') if figure_html else ''}
  {('<div class="gaps"><b>内容缺：</b><ul>' + gaps + '</ul></div>') if gaps else ''}
  {('<div class="notes"><b>不确定：</b><ul>' + uncertainties + '</ul></div>') if uncertainties else ''}
  <div class="chips">{knowledge}</div>
  <details class="src-wrap"><summary>原图位置</summary><div class="sources">{sources_html}</div></details>
</article>""")

    subject_options = "".join(f'<option value="{esc(s)}">{esc(s)}</option>' for s in subjects)
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>题库 · {len(questions)} 题</title>
<style>
:root {{ --bg:#f7f7f5; --panel:#fff; --fg:#1f2328; --muted:#667085; --line:#d8dce3; --accent:#2563eb; --ok:#0f7b55; --warn:#b42318; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111418; --panel:#181d24; --fg:#e6e8eb; --muted:#98a2b3; --line:#2a313a; --accent:#7aa2ff; --ok:#34d399; --warn:#ff8a80; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.65 "Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif; }}
header.top {{ position:sticky; top:0; z-index:5; background:var(--bg); border-bottom:1px solid var(--line); padding:14px 20px; }}
h1 {{ margin:0 0 8px; font-size:20px; }}
.stats {{ display:flex; gap:16px; flex-wrap:wrap; color:var(--muted); font-size:13px; }}
.controls {{ display:flex; gap:10px; flex-wrap:wrap; margin-top:12px; }}
select,input[type=search] {{ background:var(--panel); color:var(--fg); border:1px solid var(--line); border-radius:8px; padding:7px 10px; font:inherit; }}
input[type=search] {{ min-width:260px; flex:1; }}
main {{ max-width:1180px; margin:0 auto; padding:20px; display:grid; gap:16px; }}
.card {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px 18px; }}
.card[hidden] {{ display:none; }}
.card > header {{ display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:10px; }}
.qid {{ font-weight:700; font-family:ui-monospace,Consolas,monospace; }}
.badge {{ font-size:12px; border:1px solid var(--line); border-radius:999px; padding:1px 8px; color:var(--muted); }}
.badge.ok {{ color:var(--ok); border-color:currentColor; }}
.badge.gap {{ color:var(--warn); border-color:currentColor; }}
.badge.重绘 {{ color:var(--accent); border-color:currentColor; }}
.stem {{ font-size:16px; }}
ol.options {{ list-style:none; padding:0; display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:4px 18px; margin:10px 0; }}
ol.options li {{ padding:3px 0; }}
ol.subs {{ margin:8px 0 8px 20px; padding:0; }}
.figs {{ display:flex; flex-wrap:wrap; gap:14px; margin:12px 0; }}
.tables {{ margin:12px 0; overflow-x:auto; }}
.table-wrap {{ border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:var(--panel); }}
.table-wrap table {{ border-collapse:collapse; width:100%; font-size:14px; }}
.table-wrap th,.table-wrap td {{ border:1px solid var(--line); padding:5px 8px; vertical-align:top; }}
.table-wrap th {{ background:color-mix(in srgb, var(--accent) 8%, transparent); }}
.table-cap {{ margin-top:6px; font-size:12px; color:var(--muted); }}
.table-fallback {{ white-space:pre-wrap; font-size:13px; margin:0; }}
figure.fig {{ margin:0; border:1px solid var(--line); border-radius:10px; overflow:hidden; background:#fff; max-width:100%; }}
figure.fig img {{ display:block; max-width:min(520px,100%); height:auto; }}
figure.fig figcaption {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; padding:6px 9px; font-size:12px; color:#333; background:#f4f5f7; }}
figure.fig .mini {{ color:#2563eb; }}
.fig.missing {{ padding:12px; color:var(--warn); border:1px dashed currentColor; border-radius:8px; }}
.chips {{ display:flex; gap:6px; flex-wrap:wrap; margin-top:8px; }}
.chip {{ font-size:12px; background:color-mix(in srgb, var(--accent) 12%, transparent); color:var(--accent); border-radius:999px; padding:1px 9px; }}
.gaps {{ margin-top:10px; padding:8px 10px; border-left:3px solid var(--warn); background:color-mix(in srgb, var(--warn) 8%, transparent); }}
.notes {{ margin-top:8px; color:var(--muted); font-size:13px; }}
.src-wrap {{ margin-top:12px; }}
.src-wrap summary {{ cursor:pointer; color:var(--muted); font-size:13px; }}
.sources {{ display:grid; gap:10px; margin-top:8px; }}
.source {{ position:relative; max-width:620px; border:1px solid var(--line); border-radius:8px; overflow:hidden; background:#fff; }}
.source img {{ display:block; width:100%; height:auto; }}
.source .box {{ position:absolute; border:2px solid #2563eb; background:rgba(37,99,235,.12); box-shadow:0 0 0 1px rgba(255,255,255,.6) inset; }}
.source .page-tag {{ position:absolute; right:6px; bottom:6px; font-size:11px; background:rgba(0,0,0,.55); color:#fff; padding:1px 6px; border-radius:4px; }}
#empty {{ display:none; text-align:center; color:var(--muted); padding:40px; }}
</style>
</head>
<body>
<header class="top">
  <h1>题库 · {len(questions)} 题</h1>
  <div class="stats">
    <span>完整 {complete_count}</span><span>缺口 {gap_count}</span>
    <span>配图 {len(figure_ids)}</span><span>表格 {len(table_ids)}</span><span>重绘 {redrawn_count}</span>
  </div>
  <div class="controls">
    <select id="subject"><option value="">全部学科</option>{subject_options}</select>
    <select id="completeness"><option value="">全部状态</option><option value="true">完整</option><option value="false">有缺口</option></select>
    <input id="search" type="search" placeholder="搜索题干、选项、知识点…">
  </div>
</header>
<main id="list">{''.join(cards)}</main>
<div id="empty">没有匹配的题。</div>
<script>
const cards=[...document.querySelectorAll('.card')];
const subject=document.getElementById('subject');
const completeness=document.getElementById('completeness');
const search=document.getElementById('search');
const empty=document.getElementById('empty');
function apply(){{
  const q=search.value.trim().toLowerCase();
  let shown=0;
  for(const card of cards){{
    const okS=!subject.value||card.dataset.subject===subject.value;
    const okC=!completeness.value||card.dataset.complete===completeness.value;
    const okQ=!q||card.dataset.search.includes(q);
    const show=okS&&okC&&okQ;
    card.hidden=!show;
    if(show) shown++;
  }}
  empty.style.display=shown?'none':'block';
}}
subject.addEventListener('change',apply);
completeness.addEventListener('change',apply);
search.addEventListener('input',apply);
</script>
<script>
window.MathJax={{tex:{{inlineMath:[['\\\\(','\\\\)'],['$','$']],displayMath:[['\\\\[','\\\\]']]}},svg:{{fontCache:'global'}}}};
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-svg.js"></script>
</body>
</html>
"""
    return html_doc, problems


def main() -> int:
    setup_stdio()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default="out/questions.json")
    parser.add_argument("--figures", default="out/figures")
    parser.add_argument("--out", default="out/questions.html")
    args = parser.parse_args()

    questions_path = Path(args.questions)
    figures_dir = Path(args.figures)
    out_path = Path(args.out)
    if not questions_path.is_file():
        print(f"missing {questions_path}", file=sys.stderr)
        return 2
    if not (figures_dir / "index.json").is_file():
        print(f"missing {figures_dir / 'index.json'}", file=sys.stderr)
        return 2

    document, problems = build(questions_path, figures_dir, out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(document, encoding="utf-8")
    print(f"wrote {out_path}")
    if problems:
        print(f"{len(problems)} problem(s):", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
