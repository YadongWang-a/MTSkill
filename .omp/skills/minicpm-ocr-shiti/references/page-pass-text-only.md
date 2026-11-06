# 逐页提取：纯文本模式（不读图）

只用于**纯印刷页**或速度试验。输入只有 OCR JSON，不读 `pageimg/`、不读 crop、不读 table 切图。没有图像时，手写/印刷区分和 OCR 纠错都不可靠；不确定的项必须写进 `uncertainties`，不要猜。

## 输入

- `out/ocr/<image_id>.json` 的 `response.ocr.blocks`；
- `out/figures/index.json`、`out/tables/index.json` 的本页条目。

## 规则

1. **切题**：按题号 `1.` `2.` `（1）` `一、` 切；一个 block 可能含多题，按题号在 `content` 中的位置切；切不准就把该 block 标进 `uncertainties`。
2. **选项**：`[A]`/`A.` 起头；同一 block 多行选项全部合并。缺选项不补。
3. **bbox**：题目框 = 所分 block 的 `bbox` 并集；跨题共用块时 `bbox=null`，并在 `uncertainties` 写“共用块，框为近似”。
4. **手写**：没有图，无法判断笔迹。只删除明确的答案句式（如题干末尾孤立的 `C`、`(B)`、`D`）时，必须在 `uncertainties` 说明；拿不准就保留原文并标不确定。**不要声称已完全去笔迹。**
5. **表格**：`table` 块进 `tables[]`；`ocr_content` 为空/`<otsl>`/明显错行时，不读切图，写 `content_gaps: ["表格需图像转写"]`，不要伪造。
6. **配图**：`image/chart` 只按 `figures/index.json` 的 id/role 绑；题干有“如图”且 bbox 最近才绑；不读图确认。
7. **跨页**：只打 `continues_next/continued_from_prev`，不在这里拼接。
8. **字段**：与 `$SK/references/page-pass.md` 相同；`type/options/subquestions/figures/tables/uncertainties` 必须齐全。

## 完成判据

结构与图像模式相同，但额外接受 `uncertainties` 中的“纯文本模式无法确认笔迹/表格/图”条目；不要因为没读图而补内容。
