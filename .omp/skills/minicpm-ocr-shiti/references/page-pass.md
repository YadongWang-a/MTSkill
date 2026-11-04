# 逐页提取：只收印刷层

输入：`out/ocr/<image_id>.json` 的 `response.ocr.blocks` + 原页图 `out/pageimg/<image_id>.jpg`（已旋转矫正）。
输出：`out/pages/<image_id>.json`，一个 JSON，无解释文字。

## 判定顺序

1. **先划掉笔迹层**：铅笔画线、钢笔步骤、圈选项、勾/叉、页边批注、草稿计算、写在图上的标注。OCR 若把它们识别成文字，直接丢弃；不要写进 `stem / options / subquestions`，也不要写进 `ignored_blocks` 的 reason 之外。
2. **再按印刷题号切题**：`1.`、`2.`、`（1）`、`一、`、`例 3`。一个小题的 `(1)(2)` 留在 `subquestions`，不拆成独立题。
3. **最后绑图**：只绑本题范围内、或被题干明确引用（“如图”“下图”“图中”）的 `image / chart / table` 块。跨题共用的图（如整页一张大图）绑给真正引用它的题。

## 字段

```json
{
  "page": 0,
  "image_id": "p000",
  "questions": [
    {
      "local_id": "p000-q1",
      "number": "1",
      "type": "choice",
      "stem": "已知函数 \(f(x)=x^2-2x\)，则其对称轴为（ ）",
      "options": [
        {"key": "A", "text": "\(x=1\)"},
        {"key": "B", "text": "\(x=-1\)"}
      ],
      "subquestions": [],
      "block_ids": [3, 4],
      "bbox": [120, 240, 900, 410],
      "figures": [
        {"figure_id": "p000_b007", "role": "题干配图"}
      ],
      "tables": [
        {"table_id": "p000_t018", "block_id": 18, "format": "markdown",
         "content": "| 实验编号 | 实验操作 | 实验现象 |\n|---|---|---|\n| ① | … | … |",
         "source": "ocr", "note": "表 1"}
      ],
      "continues_next": false,
      "continued_from_prev": false,
      "uncertainties": []
    }
  ],
  "ignored_blocks": [
    {"order": 1, "reason": "页眉"},
    {"order": 9, "reason": "手写草稿"}
  ]
}
```

- `type`：`choice / fill / solve / judge / other`。有 A-D 选项就是 `choice`。
- `options`：只收印刷选项；缺选项就留空并在 `uncertainties` 写“选项缺失”，不要补。
- `subquestions`：字符串数组；若某个小问单独占块，用 `{"text":"…","block_ids":[11]}`，便于块归属；合并时转回字符串。
- `continued_from_prev=true` 且本页只有选项/小问时，`stem` 可以为空；其余情况 `stem` 不得为空。
- `bbox`：本题所有 `block_ids` 的并集；拿不准就 `null`，不要目测。`block_ids` 必须来自本页 OCR 的 `order`。
- `figures[].figure_id`：格式 `p000_b007`，对应 OCR 块 `order=7`。一个块最多绑一次；同一图被多题引用时，在各题都写同一个 id。
- `tables[]`：`table` 块只做文本表格，不进 `figures`。`table_id` 格式 `p000_t018`，`block_id` 对应 OCR `order`；`format` 是 `markdown` 或 `html`；`source` 是 `ocr` 或 `llm`。
- `continues_next`：题干/选项在页底被截断、下一页接着印。`continued_from_prev`：本页开头是上一页的续。
- `uncertainties`：只写识别层面的不确定（“第 2 行公式模糊”“选项 C 被裁断”），不写学生作答。

## 表格

表格是**文本**，不是配图。

1. 先读 `out/tables/index.json` 里本页的 `table` 块：`ocr_format`、`ocr_content`、`path`。
2. `ocr_content` 是完整 HTML/OTS L/文本时，转成 `format="markdown"` 的管道表（简单表）或保留 `format="html"`（合并单元格、复杂表头），`source="ocr"`。
3. `ocr_content` 为空、只有 `<otsl>`、明显错行/缺列时，读 `path` 切图，由 LLM 重转成 Markdown/HTML，`source="llm"`；拿不准的单元格写进 `uncertainties`，不要猜。
4. 表格绑给真正引用它的题（“如下表/如表/根据表中数据”），不要因为 bbox 相邻就绑给上一题或下一题。
5. 续表（“续表”）在本页保留独立 `tables[]` 条目，`note` 写“续表”；跨页合并阶段再拼成一张。
6. 表格的内容不重复写进 `stem`；题干保留“如下表所示”，表格放 `tables[]`。



## 块内换行

OCR 的一个 block 是**版面块**，不是一行。块内换行只是排版折行，必须全部合并后再判断：

- 选项 `[D]… $Ca(ClO)_{2}$ 不\n稳定，易分解` → 完整文本是“…… \(Ca(ClO)_{2}\) 不稳定，易分解”，不能只取第一行。
- 公式跨行（`$` 或 `\(` 被换行拆开）要接回去；任何在公式中间停下的选项都算截断。
- 同一块里可能同时有题干尾部和下一题选项（双栏/跨栏），按题号切开，但每个选项自己的多行必须收全。

## 公式

- 行内 `\(...\)`，独立 `\[...\]`，化学 `\ce{}`，分段函数 `\begin{cases}`。
- 上标/下标/根号/分数按印刷结构写；OCR 的 `x2`、`sin-1` 这类要按图修正为 `x^2`、`\sin^{-1}`。
- 图里的坐标轴标签、刻度、角度、顶点字母要进题干或 `uncertainties`，不能丢。

## 完成判据

- 每个带文字的 `text / content / doc_title / paragraph_title / display_formula / inline_formula / table / chart` 块，要么出现在某题 `block_ids`，要么在 `ignored_blocks` 有原因。
- 每个 `image / chart / table` 块，要么被某题 `figures` 引用，要么在 `ignored_blocks` 有原因。
- 题号不重、不漏；`local_id` 在本页唯一。
- 正文中没有任何学生笔迹或答案；没有 `answer / student_answer` 字段。
- 跨页只打标记，不在这里拼接。
