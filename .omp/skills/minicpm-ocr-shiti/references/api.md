# MiniCPM-OCR 托管 API

Base URL：国际 `https://api.modelbest.ai`，国内 `https://api.modelbest.cn`，必须与账号区域一致。
认证：`Authorization: Bearer $MODELBEST_API_KEY`。Key 从 Console → API Keys 创建；按 user + project 隔离。

`.env`（项目根，勿提交）：

```dotenv
MODELBEST_API_KEY=sk-...
# 可选，默认国际站
MODELBEST_BASE_URL=https://api.modelbest.ai
```

## 图片流水线

一张图片两步：上传拿 `file_id`，再同步解析。

```bash
curl --fail-with-body "$BASE/v1/files" \
  -H "Authorization: Bearer $MODELBEST_API_KEY" \
  -F 'file=@page.jpg'
# -> {"file_id":"file_abc123"}

curl --fail-with-body "$BASE/v1/document-parse" \
  -H "Authorization: Bearer $MODELBEST_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"model":"MiniCPM-OCR-1.0","file_id":"file_abc123","upright":true,"recognize_seal":true}'
```

同步接口只收**一张图片**，不能用 multipart，也不收 PDF。PDF 走 `POST /v1/document-parse/jobs`（multipart 或 `file_id`/`url`），202 后轮询 `GET /v1/document-parse/jobs/{id}`，终态 `Success / Failed / Cancelled`。

| 限制 | 值 |
|---|---|
| 上传文件 | 64 MiB，空文件拒绝 |
| 图片 | 24 MiB |
| 同步 JSON 请求体 | 32 MiB |
| PDF | ≤500 页，服务端 144 DPI，单页 ≤8192 px、≤4000 万像素 |
| 作业/结果保留 | 24 小时 |
| 活跃作业 | 每 user/project 最多 2 个 |

响应就是页面对象：

```json
{
  "metadata": {"page_index": 0},
  "choices": [{"message": {"content": "# ..."}}],
  "ocr": {
    "image_width": 1176,
    "image_height": 1664,
    "blocks": [
      {"order": 3, "label": "text", "bbox": [120,240,900,410],
       "regions": [[120,240,900,410]], "content": "...", "status": "ok"}
    ],
    "rotation": {"enabled": true, "applied": false, "total_angle": 0}
  }
}
```

`blocks[].label` 常见值：`text / content / doc_title / paragraph_title / table / display_formula / inline_formula / chart / image / header_image / footer_image / seal / header / footer / number / footnote / aside_text`。
`status`：`ok / failed / skipped_no_vlm / skipped_seal`。`skipped_*` 仍可能有框，配图照切。

`table` 块的 `content` 可能是 HTML 表、OTS L 或纯文本。它是**文本来源**，不是配图：`crop_figures.py` 把它切到 `out/tables/` 供 LLM 兜底，最终题库用 `tables[]` 的 Markdown/HTML，不进 `figures`。

## 坐标系

- `bbox` 是 `[x1,y1,x2,y2]`，左上原点，单位像素。
- 坐标空间是 `ocr.image_width × ocr.image_height` 的**处理后页面**，不是原始文件像素。
- `rotation.applied=true`：先把原图按 `total_angle` **逆时针**旋转并 `expand`，再缩放到 `image_width × image_height`，然后才能按 `bbox` 裁剪。`pageimg/` 就是这么生成的。
- `regions` 是块的全部覆盖框；多段合并时 `bbox` 只是首框，切图取所有 `regions` 的并集。
- 不要把原图坐标和图框混用；HTML 高亮只用 `pageimg` + 同一坐标系。

## 错误与重试

| HTTP | 含义 | 动作 |
|---|---|---|
| 400 / 413 | 参数、格式、尺寸不合法 | 修输入，不重试 |
| 401 / 403 | Key、账号或项目无权限 | 检查 `.env` 与区域 |
| 402 | Credits 不足 | 充值或换免费模型，不重试 |
| 404 | file/job 不属于当前项目 | 重新上传 |
| 409 | `Idempotency-Key` 与内容不一致 | 换 Key 或复用同一输入 |
| 429 | 限流 | 按 `Retry-After` 或指数退避 |
| 5xx | 上游临时故障 | 退避重试，保留 `X-Request-Id` |

幂等键只用于 JSON `file_id`/`url` 的异步提交，multipart 带键会 400。同步接口不要并发轰炸；`ocr_pages.py` 默认 4 并发，连接池复用，429/连接重置按 jitter 退避；触发 429 就降到 2–3。

## 脚本行为

`scripts/ocr_pages.py`：

- 按自然序给图片编号 `p000, p001...`，源路径写进 manifest；重跑按 `image_id` 跳过。
- 每页保存 `{image_id, source, page_index, model, file_id, preprocess, response}`；`preprocess` 记录是否缩图、原图尺寸、上传尺寸。
- 长边超过 `--max-side`（默认 2500）先缩图再上传；`--max-side 0` 关闭。`crop_figures.py` 用原图裁剪，按 OCR 尺寸比例换算 bbox，不损失配图细节。
- `--dry-run` 只列清单和 resize 动作，不联网；`--check` 验 JSON 数与 `blocks` 非空。
- 改 `--max-side` 不会自动重跑已有页；加 `--force` 才会重新上传/识别。
- 运行结束打印 `retries / rate_limited / connection_errors`，用来判断该不该调 `--workers`。
- 只有 `SOURCE` 指向的图片存在时才能切图；移动原图会破坏 `crop_figures.py`。
