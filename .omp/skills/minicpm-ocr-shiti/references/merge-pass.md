# 合并：跨页、分类、完整性

输入：`out/pages/*.json`（逐页结果）+ `out/figures/index.json`。
输出：`out/questions.json`，严格按 `$SK/references/schema.json`。

## 合并跨页

只有同时满足以下三条才合并：

1. 上一页题 `continues_next=true`，下一页题 `continued_from_prev=true`；
2. 两段题号一致，或下一页开头是选项/小问的连续内容；
3. 语义接得上（句子、选项、小问不断裂）。

照片顺序不等于页序：`p003` 可能接着 `p007`。只按相邻页的标记合并会拼错；先按题号、题干和学科核对，再决定。

题多时按学科/章节拆成 `out/question_parts/*.json`，每个 part 只写 `{"questions":[...]}`；最后用 `scripts/merge_question_parts.py` 合并、重编 `q001...`、回填 `meta`。

合并后把逐页的 `subquestions` 对象 `{"text":…,"block_ids":…}` 拍平成字符串；最终 JSON 不接受对象。

合并后：

- `pages`：涉及的页号，升序；`id` 按 `(pages[0], bbox.y, bbox.x)` 排序后重编 `q001...`。
- `parts[]`：按**内容顺序**排，题干所在段在前；每段保留 `{page, image_id, bbox, block_ids}`，原始框不丢。
- 顶层 `bbox`：取 `parts[0]`（题干段）；续页 `parts[].bbox` 保留，但顶层不再画框。
- 占位句（“接上一页”“本页仅余该题选项”“续”）从 `stem/options` 剥掉；不要留成正文。
- 判不准就拆成两题，各自 `complete=false`，`content_gaps` 写“跨页接续无法确认”。

## 题目字段

```json
{
  "id": "q001",
  "pages": [0],
  "bbox": [120, 240, 900, 410],
  "parts": [{"page": 0, "image_id": "p000", "bbox": [120,240,900,410], "block_ids": [3,4]}],
  "type": "choice",
  "stem": "已知函数 \(f(x)=x^2-2x\)，则其对称轴为（ ）",
  "options": [{"key": "A", "text": "\(x=1\)"}, {"key": "B", "text": "\(x=-1\)"}],
  "subquestions": [],
  "subject": "数学",
  "chapter": "二次函数",
  "knowledge_points": ["对称轴", "顶点式"],
  "complete": true,
  "content_gaps": [],
  "figures": [
    {
      "id": "p000_b007",
      "kind": "function",
      "role": "题干配图",
      "crop": "figures/p000_b007.png",
      "preferred": "crop",
      "redraw_svg": null,
      "redraw_png": null,
      "self_check": null,
      "review_note": null
    }
  ],
  "tables": [
    {
      "table_id": "p000_t018",
      "block_id": 18,
      "format": "markdown",
      "content": "| 实验编号 | 实验操作 | 实验现象 |\n|---|---|---|\n| ① | … | … |",
      "source": "ocr",
      "note": "表 1"
    }
  ],
  "uncertainties": []
}
```

### 学科 / 章节 / 知识点

- `subject`：`语文 / 数学 / 英语 / 物理 / 化学 / 生物 / 历史 / 地理 / 政治 / 其他`。判不出写 `"未判定"`。
- `chapter`：具体到教材章节或题型（“二次函数”“全等三角形”“文言文阅读”），不写“数学”“综合”。
- `knowledge_points`：1–3 个可复习的具体点（“顶点式”“勾股定理”“列方程解应用题”），不写空泛的“计算”“理解”。

### 完整性

`complete` 只描述**题目内容**，不描述分类置信度。

- `true`：题干、全部小问、全部选项（choice）都在，且没有截断。
- `false`：`content_gaps` 必须非空，逐条写缺什么（“选项 C 被裁断”“第 (2) 问未拍到”“配图下半页缺失”）。
- 分类不确定写进 `uncertainties`，不要把 `complete` 拉成 `false`。

## 配图分类

按切图 + 题干判 `kind`：

| kind | 判据 |
|---|---|
| `function` | 坐标轴 + 曲线/直线/抛物线，或题干要求画函数图象 |
| `geometry` | 三角形、圆、角、平行线、立体图，标注顶点/边长/角度 |
| `chart` | 柱状/折线/扇形/统计图，带刻度或图例 |
| `apparatus` | 化学实验装置：试管/烧瓶/洗气瓶/铁架台/尾气处理/装置连接图；**不重绘** |
| `photo` | 实物照片、插图、人物 |
| `other` | 以上都不是 |

表格不进这张表：`table` 块一律走 `tables[]` 文本，不切图、不重绘。化学科目的实验装置必须选 `apparatus`，保留原切图；`function / geometry / chart` 默认进入重绘候选；`photo / other` 默认切图，只有明确可画且值得画时才标 `redraw_candidate=true`。拿不准就不标。`crop` 从 `figures/index.json` 取，不要自己编路径。

## 表格合并

- 逐页的 `tables[]` 原样带进最终题；`table_id`、`block_id`、`format`、`source` 不改。
- 同一张表跨页/续表：按表头对齐，把后续行并进第一张表，保留一个 `table_id`，`note` 写“含续表”；不要留两张截图。
- 同一题多张独立表：保留多条 `tables[]`，按出现顺序排列，`note` 写“表 1/表 2”。
- 表格内容为空或仍是 `<otsl>`：退回 `out/tables/<table_id>.png` 让 LLM 重转，`source="llm"`；转不准就在 `content_gaps` 写“表格未能转写”。
- 最终 `figures` 里不允许 `kind="table"`；validator 会报错。

## 去重与顺序

- 同一道题被相邻页重复识别：保留内容更完整的一段，另一段并进 `parts`，不要出两条。
- 选项按 `A/B/C/D` 排序；小问按 `(1)(2)(3)` 排序。
- 没有题号的零散印刷文字（页眉、页脚、评分栏）不进题库；若属于某题（如“（满分 10 分）”），并入题干或 `uncertainties`。

## 完成判据

`python "$SK/scripts/validate.py" --questions out/questions.json --figures out/figures --ocr out/ocr` 为 0 problem：

- `id` 唯一，`meta.question_count` 与实际条数一致；
- 每题 `pages / type / subject / chapter / knowledge_points / complete` 齐全；
- `choice` 完整时至少 2 个选项且 key 不重复；不完整时用 `content_gaps` 说明缺项；
- `complete=false` 时 `content_gaps` 非空，反之亦然；
- 每个 `figures[].id` 在 `figures/index.json` 里，`crop` 文件存在；没有 `kind="table"` 的图；
- 每个 `tables[]` 有 `table_id / block_id / format / content / source`，Markdown 表有分隔行；
- 没有 `answer / student_answer` 字段，正文没有学生作答。
