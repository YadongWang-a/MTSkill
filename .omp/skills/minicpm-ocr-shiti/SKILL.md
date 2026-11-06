---
name: minicpm-ocr-shiti
description: 把练习册/试卷照片批量转成题库：MiniCPM-OCR 提取印刷文本与版面框，忽略手写草稿，切出配图，配图尝试重绘（化学装置图除外），输出带学科/章节/知识点/完整性的 JSON 与浏览 HTML。Use when the user wants 照片转题库、批量提取带图题目，or mentions MiniCPM-OCR.
---

# 照片 → 题库

一叠照片 → 一个可复核的题库。整条流水线只认**印刷层**：印刷文字、表格、公式、图形进题库；手写答案、草稿、批注、勾叉一律不进正文。笔迹不是噪声，是另一层——不转录、不猜测、不让它污染题干。

| 产物 | 内容 |
|---|---|
| `out/ocr/` | 每页 MiniCPM-OCR 原始 JSON：`blocks[]` 的文本、`bbox`、阅读顺序 |
| `out/figures/` | 配图切图 + `index.json`（`figure_id` → 页/框/文件） |
| `out/tables/` | 表格块的内部切图 + `index.json`，供 LLM 兜底转写；**不直接进题库** |
| `out/pageimg/` | 旋转矫正后的页面图 + 尺寸，HTML 高亮用 |
| `out/pages/` | 逐页提取：题目、选项、小问、`block_ids`、文本表格、配图引用 |
| `out/question_parts/` | 题多时按学科拆的中间题库；`merge_question_parts.py` 合并 |
| `out/questions.json` | 最终题库：题干/选项/小问、文本表格、学科/章节/知识点、`complete` + `content_gaps`、配图（切图/重绘） |
| `out/questions.html` | 浏览页：筛选、搜索、原图定位、重绘/切图对比 |

依赖：Python 3.10+、Pillow、requests；OCR 走 ModelBest 托管 API，项目根 `.env` 里放 `MODELBEST_API_KEY`（见 `.env.example`）。图片会上传到 ModelBest；不能出域的资料改用本地部署。

脚本在技能目录。下文用 `SK` 指代：

```bash
SK="${MINICPM_OCR_SKILL_DIR:-$(realpath skill://minicpm-ocr-shiti 2>/dev/null || echo "$PWD/.omp/skills/minicpm-ocr-shiti")}"
```

## 硬约束

- **只收印刷层**：题干、选项、小问只保留印刷内容。学生写的答案、步骤、批注、圈画一个字都不进 JSON；没有 `student_answer` 字段。
- **坐标只信 OCR**：题框与图框来自 `ocr.blocks[].bbox`，不靠目测。`rotation.applied=true` 时坐标系是矫正后的页面，页面图与框必须来自同一坐标系。
- **配图切图，表格转文本**：`image / chart` 切图；`table` 不切进 `figures`，一律转成 `tables[]` 里的 Markdown/HTML 文本。MiniCPM-OCR 的表格内容为空、`<otsl>`、或明显错行时，读 `out/tables/<table_id>.png` 交给 LLM 重转，`source="llm"`。**只重绘候选图**：默认 `function / geometry / chart`，以及显式 `redraw_candidate=true` 的图；`apparatus / photo / other` 默认直接 `preferred="crop"`。化学装置图永不重绘。每个候选只给一次机会，独立复核不过就回退原切图。
- **不补不编**：照片拍不到、被裁断、缺选项的，写 `content_gaps` 并把 `complete` 置 `false`；不猜内容，不用“略”“同上”填空。
- **公式可渲染**：行内公式用 `\(...\)`，独立公式用 `\[...\]`，化学用 `\ce{}`。JSON 与 HTML 共用这一份文本，不存两份。

## 步骤

### 0. 配置 Key

项目根没有 `.env` 就 `cp "$SK/.env.example" .env`，填入 `MODELBEST_API_KEY`；国际站用 `https://api.modelbest.ai`，国内站用 `https://api.modelbest.cn`。不要把 `.env` 提交。

完成判据：`python "$SK/scripts/ocr_pages.py" --input pic --out out/ocr --dry-run` 打印 `key=set`。

### 1. 全量 OCR

```bash
python "$SK/scripts/ocr_pages.py" --input pic --out out/ocr --workers 4 --max-side 2500
```

逐张上传 → 同步解析 → 落盘 `out/ocr/<image_id>.json`；4 个 worker 并发，连接复用，429/连接重置按 jitter 退避；长边超过 2500 px 先缩图再传（`--max-side 0` 关闭）。已完成的跳过；先跑 `--dry-run` 看清单和每张图的 resize 动作。改 `--max-side` 后旧页仍会复用，要重跑加 `--force`。

完成判据：`python "$SK/scripts/ocr_pages.py" --check --out out/ocr` 输出 `ok`；图片数 == JSON 数，且每页 `ocr.blocks` 非空。

### 2. 切配图与表格底图

```bash
python "$SK/scripts/crop_figures.py" --ocr out/ocr --out out/figures --table-out out/tables --page-out out/pageimg
```

`image / chart` 切到 `out/figures/`；`table` 切到 `out/tables/`，只作为 LLM 兜底转写的底图。页面按 `rotation.total_angle` 矫正后存 `out/pageimg/`。切图坐标与页面图同源，后续 HTML 直接按百分比叠加。

完成判据：`figure_id` 数 == 可用配图块数；`table_id` 数 == `table` 块数；`out/figures/index.json` 与 `out/tables/index.json` 的每条记录都有存在的 PNG；`out/pageimg/` 页数 == OCR 页数。

### 3. 逐页提印刷层

一页一个 subagent：读整页图 + 该页 OCR JSON + `out/tables/index.json`，按 `$SK/references/page-pass.md` 写 `out/pages/<image_id>.json`。表格块必须进入某题的 `tables[]`；OCR 表格为空/OTS L/错行时，读 `out/tables/<table_id>.png` 由 LLM 转写。派单时抄进预算：**≤4 次工具调用，不重读、不截小图**。

完成判据：`python "$SK/scripts/validate.py" --pages out/pages --ocr out/ocr` 0 problem；每个有文字的 block 要么进某题的 `block_ids`，要么在 `ignored_blocks` 写明原因；每个 `table` block 要么进 `tables[]`，要么被忽略；`stem / options / subquestions` 里没有手写内容。

### 4. 合并跨页 + 定字段

一个 agent 读全部 `out/pages/*.json` + `out/figures/index.json`，按 `$SK/references/merge-pass.md` 写 `out/questions.json`。表格在 `tables[]` 内合并（续表并成一张），不转成配图。题多（>20 题）就先按学科/章节拆到 `out/question_parts/*.json`，再合并：

```bash
python "$SK/scripts/merge_question_parts.py" --parts out/question_parts --out out/questions.json
```

跨页题只保留首页框，续页 `bbox=null`，两段原始框留在 `parts[]`。

完成判据：`python "$SK/scripts/validate.py" --questions out/questions.json --figures out/figures --ocr out/ocr` 0 problem；`id` 唯一；每题 `subject / chapter / knowledge_points / complete` 齐全；每个 `figure_id` 都能解析到切图；表格是 `tables[]` 文本，不是 `figures`。

### 5. 重绘候选（4 张一批、硬预算、批量复核）

**候选范围**：默认只画 `function / geometry / chart`；`apparatus / photo / other` 默认切图。确实值得画的 `photo / other`，在 merge 阶段标 `redraw_candidate=true`。化学装置图 `apparatus` 永远不画。

```bash
python "$SK/scripts/redraw_plan.py" --questions out/questions.json --out-dir out/redraw --batch-size 4
```

按 `out/redraw/plan.json` 的批次执行，**4 张一批流水线**：

1. 每批最多 4 个绘图 subagent，每个**硬预算 3 次工具调用 / 180 秒**；超时或没落盘 SVG，主 agent 直接 `write proc://<agent>/kill`，不回等、不重试。
2. 渲染当前批：
   ```bash
   python "$SK/scripts/render_batch.py" --plan out/redraw/plan.json --batch batch_01 --workers 4 --timeout 90
   ```
3. 生成对比拼图：
   ```bash
   python "$SK/scripts/contact_sheet.py" --plan out/redraw/plan.json --batch batch_01
   ```
4. **一个 reviewer 看一张 4 图拼图**（不是每图一个 reviewer），按 `$SK/references/redraw-pass.md` 输出 `out/redraw/reviews/batch_01.json`；reviewer 同样 **2 次调用 / 180 秒**硬预算，超时整批回退 crop。复核期间可以同时开下一批绘图，总并发控制在 4–6。
5. 批复核完成后套用结果：
   ```bash
   python "$SK/scripts/apply_redraw_reviews.py" --questions out/questions.json --plan out/redraw/plan.json --reviews out/redraw/reviews --parts out/question_parts
   ```

`pass` → `preferred="redrawn"`；`fail`/超时/未复核 → `preferred="crop"`，`review_note` 写原因。每个候选只尝试一次，不二次修图。硬闸：`preferred=redrawn` 必须同时有 `redraw_svg`、`redraw_png`、`self_check=passed`。

完成判据：每张图都有 `preferred`；候选全部有 pass/fail 或超时回退；非候选（apparatus/photo/other）全部 `preferred="crop"`；`out/redraw/review/` 每批有一张拼图；validator 0 problem。

### 6. 验收 + HTML

```bash
python "$SK/scripts/build_html.py" --questions out/questions.json --figures out/figures --ocr out/ocr --out out/questions.html
```

在浏览器里抽查：卡片数 == `meta.question_count`；学科筛选、完整性筛选、搜索都生效；每张图能加载；重绘图上的“原切图”链接能打开；**每张卡片都显示原图缩略图，并用框标出该题位置**；共享块的回退框为虚线并注明“按块近似”；表格按行列渲染，不是截图。公式由 MathJax CDN 渲染，离线打开时保留原始 LaTeX。

完成判据：HTML 无 404 图片；validator 仍 0 problem；报告题数、配图数、表格数、重绘数、完整/缺口数。

## 参考文件

| 何时读 | 文件 |
|---|---|
| 改 OCR 调用、坐标换算、错误重试 | `$SK/references/api.md` |
| 逐页提取的字段与判定 | `$SK/references/page-pass.md` |
| 跨页合并、学科/章节/知识点、完整性 | `$SK/references/merge-pass.md` |
| 重绘规则与独立复核 | `$SK/references/redraw-pass.md` |
| 最终 JSON 契约 | `$SK/references/schema.json` |

## 失败处理

| 现象 | 处理 |
|---|---|
| 401 / 403 | 检查 `.env` 的 Key 与 API 区域；`MODELBEST_BASE_URL` 与账号区域一致 |
| 402 | Credits 不足；换限时免费模型或充值，不要重试 |
| 429 | 降低 `--workers`，按 `Retry-After` 退避；异步任务最多 2 个活跃 |
| 某页 `blocks` 为空 | 重新上传该页；仍为空则在 `out/ocr/manifest.json` 标记并跳过，不伪造 |
| 跨页判不准 | 宁可拆成两题并在 `content_gaps` 说明，不做错误拼接 |
| 重绘复核失败 | 保留切图，`preferred=crop`，`self_check=failed` + 原因 |
