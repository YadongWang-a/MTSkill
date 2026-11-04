# MTSkill

练习册/试卷照片 → 可复核题库。

- OCR：MiniCPM-OCR（ModelBest 托管 API）
- 提取：只保留印刷层，忽略手写、答案、草稿
- 配图：切图；所有配图尝试重绘，独立复核不过则回退原图
- 表格：不截图，转成 Markdown/HTML 文本
- 输出：`questions.json` + `questions.html`

## Skill

```text
.omp/skills/minicpm-ocr-shiti/
├─ SKILL.md
├─ references/
└─ scripts/
```

## 快速开始

```bash
cp .omp/skills/minicpm-ocr-shiti/.env.example .env
# 填入 MODELBEST_API_KEY

SK=.omp/skills/minicpm-ocr-shiti
python "$SK/scripts/ocr_pages.py" --input pic --out out/ocr --workers 4 --max-side 2500
python "$SK/scripts/crop_figures.py" --ocr out/ocr --out out/figures --table-out out/tables --page-out out/pageimg
```

后续按 `SKILL.md` 的步骤做逐页提取、合并、重绘、校验和 HTML。

## 示例图片

`pic/` 下是 11 张练习册照片，用于端到端测试。

## 注意

- `.env`、`out/`、`__pycache__/` 已在 `.gitignore` 中忽略。
- 托管 OCR 会把图片上传到 ModelBest；不能出域的资料请本地部署。
