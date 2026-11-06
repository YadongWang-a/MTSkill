# 重绘与独立复核

只重绘候选：默认 `function / geometry / chart`，以及显式 `redraw_candidate=true` 的图。**`apparatus / photo / other` 默认切图**；化学装置图永不重绘。表格走 `tables[]` 文本，不在此列。

三条硬规则：

- **4 张一批**：`redraw_plan.py` 分批；每批最多 4 个绘图 agent，总并发 4–6。画完批渲染、拼图、复核，同时可开下一批。
- **硬预算**：每个绘图 agent 最多 3 次工具调用 / 180 秒；到点没落盘 SVG，主 agent 直接 `proc://<id>/kill`，按超时回退切图。不靠 agent 自觉。
- **一次机会 + 批量复核**：每张图只画一次；每批用一张 3–5 图拼图交给**一个**没参与绘制的 reviewer，不每图一个 reviewer。**每个 batch 最多复核 2 次**，每次最多 **2 次工具调用**（读拼图 + 写 JSON），不打开单图；2 次后仍 fail/无结论，整批回退 crop。失败不重画。

目标不是“画得好看”，是**结构可替代原图**：曲线走向、交点、标注、刻度、相对位置都对得上。照片/复杂插图也一样判，画不忠实就回退。

## 画之前

读两样东西：

1. `out/figures/<id>.png` 切图（唯一几何依据）；
2. 该题 `stem`（决定图上必须出现哪些标注）。

切图存在但模糊/缺半边/关键标注被裁掉：仍然画一次，让 reviewer 判；不要在这里自行放弃。切图文件缺失或完全不可读：直接 `preferred="crop"`，`self_check="failed"`，`review_note` 写“切图缺失：…”。

## SVG 规则

- 单文件、自包含；不引用外部图片、字体、脚本。
- `viewBox` 紧贴内容，留 12–24 px 边距；不要用固定 `width/height` 锁死。
- 白底黑线；线宽 1.5–2；坐标轴带箭头；刻度、单位、图例、顶点字母、角度弧线都要有。
- 中文与数学混排：`font-family="Microsoft YaHei, Noto Sans CJK SC, sans-serif"`；公式用 `<text>` + `tspan` 写可读形式，不依赖 MathJax。
- 曲线用 `<path>`，采样点足够（抛物线/三角函数至少 60 点）；不要用折线糊弄曲线。
- 几何图按原图比例；直角符号、等长标记、平行箭头、虚线辅助线保留。
- 统计图必须有轴标签、刻度、数据标签；数值只能来自原图或题干，**不补、不估**。
- 图上不出现学生笔迹、答案、勾叉。

## 渲染

```bash
python "$SK/scripts/render_svg.py" out/figures/<id>.svg out/figures/<id>_redraw.png
```

`<id>.png` 是原切图，不能覆盖；重绘固定用 `<id>_redraw.png`。

渲染后检查：PNG 最短边 ≥800 px；没有裁切；文字不重叠；线条不发虚。渲染失败就回退切图，不交半成品。

## 分批流水线

```bash
python "$SK/scripts/redraw_plan.py" --questions out/questions.json --out-dir out/redraw --batch-size 4
```

1. 按 `out/redraw/plan.json` 的 `batch_01…` 顺序处理；每批最多 4 张。
2. 每批派最多 4 个绘图 agent；每个只读 job + 切图、写 SVG。job JSON 的 `budget` 是 3 次调用 / 180 秒；到点由主 agent 强杀。
3. 渲染该批：`render_batch.py --batch batch_01 --workers 4 --timeout 90`。
4. 生成拼图：`contact_sheet.py --batch batch_01`。
5. 一个 reviewer 看这张拼图，写 `out/redraw/reviews/batch_01.json`；每次只看拼图，不打开 crop/redraw/job。最多 2 次复核。
6. 全部批完成后：`apply_redraw_reviews.py` 统一落 `preferred/self_check/review_note`。
7. 非候选（`apparatus / photo / other` 未标 `redraw_candidate`）直接 `preferred="crop"`，不派 agent。

## 独立复核（批量）

派一个**没参与绘制**的 agent，看一张 3–5 图的拼图（左原切图、右重绘），逐图判，输出整批：

```json
{"batch":"batch_01","results":[
  {"figure_id":"p000_b007","pass":true,"reason":"曲线、刻度与标注一致"},
  {"figure_id":"p000_b012","pass":false,"reason":"多画了一根导管"}
]}
```

清单（任一不过即 `pass=false`）：

1. 图形类型一致（抛物线仍是抛物线，不是折线）。
2. 关键点一致：交点、顶点、圆心、直角、切点、极值。
3. 标注一致：轴名、刻度、单位、图例、顶点字母、边长/角度。
4. 相对位置与比例一致，没有镜像或旋转。
5. 没有原图不存在的数据、曲线或标注。
6. 文字可读，无乱码、重叠、裁切。
7. `photo / other`：主体内容、结构和关键标注必须能替代原图；只是“画了个大概”就判 fail。

复核结果由 `apply_redraw_reviews.py` 写回：

- `pass=true` → `preferred="redrawn"`, `self_check="passed"`, `review_note=reason`；
- `pass=false` / 超时 / 未复核 → `preferred="crop"`, `self_check="failed"`, `review_note=reason`，保留 SVG/PNG 供排查。

**`pass=false` 最多再复看一次**：只复核同一张重绘，不重画、不调参；第二次仍 false 就结束并回退 crop。重绘是可选增强，原切图始终是兜底。

## 硬闸

`preferred="redrawn"` 必须同时满足：

- `redraw_svg` 与 `redraw_png` 都是存在文件；
- `self_check == "passed"`；
- `review_note` 非空。

否则 `preferred="crop"`。validator 会把这些当 problem 报出来，改完重跑第 4 步校验。
