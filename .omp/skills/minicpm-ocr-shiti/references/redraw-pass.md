# 重绘与独立复核

`figures[]` 里除 `kind="apparatus"` 外的**每一张图都尝试重绘**：`function / geometry / chart / photo / other` 都画一次。**化学装置图（`apparatus`）不重绘**，直接 `preferred="crop"`；表格也不在此列，`table` 块走 `tables[]` 的 Markdown/HTML 文本。

两条硬规则：

- **并行**：一张图一个 subagent，所有绘图任务同批发出；reviewer 也同批并行，不要串行等一张画完再画下一张。
- **一次机会**：每张图只画一次、只审一次。review 不过或渲染失败就 `preferred="crop"`，不二次修图、不重试。

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

## 并行派单

1. 从 `questions.json` 收集可重绘的 `figures[].id`：跳过 `kind="apparatus"`，表题也不在内。
2. 每张可重绘图一个绘图 subagent，**同批发出**；每张只写一个 `<id>.svg`，不共享文件。
3. 所有 SVG 渲染完后，再每张可重绘图一个 reviewer subagent，**同批发出**。
4. 任何一张失败只影响它自己：`preferred="crop"`，其余照常。
5. `apparatus` 不派绘图、不派 reviewer；直接 `preferred="crop"`，`self_check=null`，`review_note="化学装置图，按规则不重绘"`。

## 独立复核

派一个**没参与绘制**的 agent，只给它三样：原切图、重绘 PNG、题干。按下面清单逐项判，输出一行：

```json
{"figure_id":"p000_b007","pass":true,"reason":"曲线开口、顶点、两条渐近线与原图一致；刻度 0/1/2 完整"}
```

清单（任一不过即 `pass=false`）：

1. 图形类型一致（抛物线仍是抛物线，不是折线）。
2. 关键点一致：交点、顶点、圆心、直角、切点、极值。
3. 标注一致：轴名、刻度、单位、图例、顶点字母、边长/角度。
4. 相对位置与比例一致，没有镜像或旋转。
5. 没有原图不存在的数据、曲线或标注。
6. 文字可读，无乱码、重叠、裁切。
7. `photo / other`：主体内容、结构和关键标注必须能替代原图；只是“画了个大概”就判 fail。

复核结果写回 `questions.json`：

- `pass=true` → `preferred="redrawn"`, `self_check="passed"`, `review_note=reason`；
- `pass=false` → `preferred="crop"`, `self_check="failed"`, `review_note=reason`，保留 SVG/PNG 供排查，但 HTML 显示切图。

**`pass=false` 就结束**：不重画、不调参、不派第二轮 reviewer。重绘是可选增强，原切图始终是兜底。

## 硬闸

`preferred="redrawn"` 必须同时满足：

- `redraw_svg` 与 `redraw_png` 都是存在文件；
- `self_check == "passed"`；
- `review_note` 非空。

否则 `preferred="crop"`。validator 会把这些当 problem 报出来，改完重跑第 4 步校验。
